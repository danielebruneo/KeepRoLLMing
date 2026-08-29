"""Minimal orchestration layer for KeepRollMing API.

This is the main entry point that delegates business logic to modular components:
- keeprollming/endpoints/ - API endpoint handlers
- keeprollming/streaming/ - SSE handling and transformations
- keeprollming/processing/ - Summarization and message processing
- keeprollming/routing/ - route resolution and HTTP client
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import (
    CONFIG,
    DEFAULTS,  # compatibility seam patched by summary integration tests
)
from .observability import events_app as _app
from .observability import events_request as _req_events
from .observability.events_streaming import emit_peer_disconnected, emit_trace_lifecycle


def _parse_captured_sse_text(sse_text: str) -> tuple[str, str | None, dict | None, int]:
    """Parse raw SSE text to extract assistant message and metadata.

    This function is used when direct chunk parsing fails (e.g., for final logging)
    to reconstruct the response from captured SSE data.

    Args:
        sse_text: Raw SSE text content

    Returns:
        Tuple of (assistant_text, finish_reason, usage_dict, event_count)
    """
    assistant_parts: list[str] = []
    finish_reason: str | None = None
    final_usage: dict | None = None
    event_count = 0
    buf = sse_text

    while True:
        m_sep = re.search(r"\r?\n\r?\n", buf)
        if not m_sep:
            break

        block = buf[: m_sep.start()]
        buf = buf[m_sep.end() :]

        data_lines: list[str] = []
        for line in block.splitlines():
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if not data_lines:
            continue

        payload_sse = "\n".join(data_lines).strip()
        if not payload_sse or payload_sse == "[DONE]":
            continue

        event_count += 1
        try:
            obj = json.loads(payload_sse)
        except Exception:
            continue

        if not isinstance(obj, dict):
            continue

        if isinstance(obj.get("usage"), dict):
            final_usage = obj.get("usage")

        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0] if isinstance(choices[0], dict) else None
            if isinstance(c0, dict):
                for candidate in (c0.get("delta"), c0.get("message")):
                    if isinstance(candidate, dict):
                        piece = candidate.get("content")
                        if isinstance(piece, str) and piece:
                            assistant_parts.append(piece)

                fr = c0.get("finish_reason")
                if isinstance(fr, str) and fr:
                    finish_reason = fr

    return "".join(assistant_parts).strip(), finish_reason, final_usage, event_count


# ----------------------------
# Application Lifecycle
# ----------------------------


async def _config_watcher():
    """Watch for configuration changes and emit them through projectors."""
    from .config import check_config_reload, get_config_mtime

    while True:
        try:
            result = check_config_reload()
            if result:
                current = get_config_mtime() or 0.0
                _app.emit_config_reloaded(
                    message="Configuration reloaded",
                    config_mtime=current,
                    dispatcher=_event_dispatcher,
                )
        except Exception as e:
            _app.emit_config_reload_failed(error=str(e), dispatcher=_event_dispatcher)

        # Check every 2 seconds
        await asyncio.sleep(2)


# ── Global EventDispatcher (O10) ──────────────────────────────────

_event_dispatcher: Optional[Any] = None
_route_status_registry: Optional[Any] = None


def get_event_dispatcher():
    """Return the global EventDispatcher singleton."""
    return _event_dispatcher


def get_route_status_registry():
    """Return the process-local rolling status registry, when initialized."""
    return _route_status_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks."""
    from .async_log_writer import get_async_writer, start_async_writer, stop_async_writer
    from .performance import set_performance_logs_dir, set_summary_interval
    from .upstream import configure_http_transport, get_http_transport_settings

    # Start the async log writer (replaces sync flush() in hot path)
    await start_async_writer()
    # Register debug sink for streaming handler
    get_async_writer().register_sink("stream_debug", "/tmp/streaming_final_chunk.log")

    # Configure performance logs directory from config
    perf_logs_dir = os.environ.get("PERFORMANCE_LOGS_DIR") or CONFIG.get("defaults", {}).get(
        "performance_logs_dir", "__performance_logs"
    )
    set_performance_logs_dir(perf_logs_dir)

    # Configure summary update interval (reduce I/O overhead)
    summary_interval = CONFIG.get("performance", {}).get("summary_update_interval", 100)
    set_summary_interval(summary_interval)
    configure_http_transport(CONFIG.get("upstream_transport"))

    # ── O10: Initialize global EventDispatcher + PerformanceConsumer ──
    global _event_dispatcher, _route_status_registry
    from .observability.consumers import PerformanceConsumer
    from .observability.dispatcher import EventDispatcher
    from .observability.route_status import RouteStatusRegistry

    _event_dispatcher = EventDispatcher()

    # The private status endpoint is event-fed and has no I/O on its hot path.
    # Seed only the same rolling window from completed records after restart.
    _route_status_registry = RouteStatusRegistry()
    await asyncio.to_thread(_route_status_registry.seed_from_performance_logs, perf_logs_dir)
    _event_dispatcher.subscribe("execution.chat", _route_status_registry)
    _event_dispatcher.subscribe("execution.streaming", _route_status_registry)
    _event_dispatcher.subscribe("execution.performance", _route_status_registry)
    _event_dispatcher.subscribe("request.lifecycle", _route_status_registry)

    # Register PerformanceConsumer for execution.performance.* namespace
    perf_consumer = PerformanceConsumer(
        perf_logs_dir=perf_logs_dir,
        summary_interval=summary_interval,
    )
    # JSONL persistence and periodic YAML summaries must not block a streaming
    # response. The consumer serialises this work on a worker thread.
    _event_dispatcher.subscribe_async(
        "execution.performance",
        perf_consumer.consume_async,
    )

    # Wire the canonical shared client into the Projector architecture.
    from .upstream import set_upstream_dispatcher

    set_upstream_dispatcher(_event_dispatcher)
    from .observability.events_upstream import emit_transport_configured

    emit_transport_configured(
        get_http_transport_settings(),
        dispatcher=_event_dispatcher,
    )

    # ── BLOCKER-001 FIX: LoggerConsumer retired from production stdout path ──
    # Previously subscribed to all root namespaces at DEBUG level, outputting
    # JSON RuntimeEvents to stdout via Python logging.StreamHandler. This
    # competed with the main projector (PLAIN, BASIC level) and contradicted
    # D-072 §6. Default projectors now own stdout/file routing.

    # ── O11: Initialize BodyCaptureConsumer for error payload capture ──
    from .observability.body_capture_consumer import BodyCaptureConsumer

    body_capture_policy = CONFIG.get("body_capture", {}).get("policy", "errors_only")
    body_capture_consumer = BodyCaptureConsumer(policy=body_capture_policy)

    # Subscribe to execution.* and request.* namespaces for error events
    _event_dispatcher.subscribe("execution.chat", body_capture_consumer)
    _event_dispatcher.subscribe("execution.streaming", body_capture_consumer)
    _event_dispatcher.subscribe("request.lifecycle", body_capture_consumer)

    # ── O12: Initialize RequestCaptureConsumer for raw request capture ──
    from .observability.request_capture_consumer import RequestCaptureConsumer

    request_capture_config = CONFIG.get("request_capture", {})
    request_capture_policy = request_capture_config.get("policy", "disabled")
    selected_routes = request_capture_config.get("selected_routes")
    request_capture_consumer = RequestCaptureConsumer(
        policy=request_capture_policy,
        selected_routes=selected_routes,
    )

    # Subscribe to request.capture namespace for raw request capture events
    _event_dispatcher.subscribe("request.capture", request_capture_consumer)

    # The same RuntimeEvent stream feeds independently configured projections.
    log_dir = os.environ.get("LOG_PATH", ".")
    observability_config = CONFIG.get("observability", {})
    from .observability.default_projectors import (
        create_default_projectors,
        start_queued_default_projectors,
        stop_queued_default_projectors,
    )

    default_projectors = create_default_projectors(log_dir, observability_config.get("projectors"))
    projector_queue_size = observability_config.get("projector_queue_size", 2048)
    queued_projectors = await start_queued_default_projectors(
        default_projectors,
        _event_dispatcher,
        max_queue_size=projector_queue_size,
    )

    from .observability.raw_trace_consumer import RawTraceConsumer

    raw_trace_config = observability_config.get("raw_trace", {})
    raw_trace_path = Path(str(raw_trace_config.get("path", "__raw_traces__")))
    if not raw_trace_path.is_absolute():
        raw_trace_path = Path(log_dir) / raw_trace_path
    raw_trace_consumer = RawTraceConsumer(
        policy=raw_trace_config.get("policy", "disabled"),
        selected_routes=raw_trace_config.get("selected_routes"),
        base_dir=raw_trace_path,
        max_bytes_per_request=raw_trace_config.get("max_bytes_per_request", 20 * 1024 * 1024),
    )
    _event_dispatcher.subscribe("transport.trace", raw_trace_consumer)

    # FIX-D072: Thread dispatcher through emit_* calls so events flow through
    # the Projector architecture instead of bypassing to legacy log().
    _app.emit_perf_logs_dir(
        message=f"Performance logs directory: {perf_logs_dir}",
        dispatcher=_event_dispatcher,
    )

    # Initial config reload check (in case config changed before startup)
    from .config import check_config_reload

    if check_config_reload():
        _app.emit_config_reloaded(
            message="Config was modified before startup, reloading...",
            dispatcher=_event_dispatcher,
        )

    # Keep ownership of the watcher so SIGTERM can await a clean cancellation
    # instead of leaving it for the event loop to tear down implicitly.
    config_watcher_task = asyncio.create_task(
        _config_watcher(), name="keeprollming-config-watcher"
    )

    _app.emit_starting(message="Starting...", dispatcher=_event_dispatcher)
    try:
        yield
    finally:
        _app.emit_stopping(
            message="Shutting down...",
            dispatcher=_event_dispatcher,
        )
        config_watcher_task.cancel()
        with suppress(asyncio.CancelledError):
            await config_watcher_task
        from .upstream import close_http_client

        await close_http_client()
        await stop_queued_default_projectors(queued_projectors)
        await stop_async_writer()


# ----------------------------
# FastAPI Application
# ----------------------------
app = FastAPI(
    title="KeepRollMing API",
    description="LLM Request Orchestrator with streaming and summarization",
    version=os.getenv("APP_VERSION", "0.1.0"),
    lifespan=lifespan,
)


class DisconnectAwareStreamingResponse(StreamingResponse):
    """SSE response that always cancels its body when the peer disconnects.

    Starlette 1.3 delegates this to a failed ``send`` for ASGI spec 2.4 and
    newer.  A client can disappear while the kernel still accepts small SSE
    writes, leaving an upstream ``httpx`` stream alive indefinitely.  This
    response owns the sole post-request ``receive`` consumer and races it
    against the body stream on every ASGI version.
    """

    @staticmethod
    def _scope_connection_data(scope: dict) -> dict[str, str]:
        """Return non-sensitive socket and protocol facts from an ASGI scope."""
        host, port = scope.get("client") or (None, None)
        peer = f"{host}:{port}" if host is not None and port is not None else ""
        return {
            "peer": peer,
            "path": str(scope.get("path") or ""),
            "http_version": str(scope.get("http_version") or ""),
            "asgi_spec": str((scope.get("asgi") or {}).get("spec_version") or ""),
        }

    @classmethod
    def _trace_lifecycle(cls, scope: dict, boundary: str, **data: Any) -> None:
        state = scope.get("state") or {}
        req_id = str(state.get("req_id") or "-")
        emit_trace_lifecycle(
            req_id,
            boundary=boundary,
            dispatcher=get_event_dispatcher(),
            **cls._scope_connection_data(scope),
            **data,
        )

    @classmethod
    def _record_downstream_abort(cls, scope: dict, *, reason: str, action: str) -> None:
        """Emit both concise and forensic records for a terminated SSE peer."""
        state = scope.get("state") or {}
        req_id = str(state.get("req_id") or "-")
        connection = cls._scope_connection_data(scope)
        dispatcher = get_event_dispatcher()
        _req_events.emit_cancelled(
            req_id=req_id,
            reason=reason,
            level="BASIC",
            dispatcher=dispatcher,
        )
        emit_peer_disconnected(
            req_id,
            reason=reason,
            action=action,
            **connection,
            dispatcher=dispatcher,
        )

    async def listen_for_disconnect(self, scope: dict, receive: Any) -> None:
        """Wait for the ASGI disconnect signal while recording its provenance.

        This is intentionally the sole post-request consumer of ``receive``.
        It preserves Starlette's semantics while making an otherwise opaque
        ``http.disconnect`` observable in an opt-in raw trace.
        """
        while True:
            message = await receive()
            message_type = str(message.get("type") or "")
            # Request bodies have already been consumed by the endpoint.  Do
            # not retain their content; only record message shape and timing.
            self._trace_lifecycle(
                scope,
                "downstream.asgi_receive",
                message_type=message_type,
                body_bytes=len(message.get("body") or b""),
                more_body=bool(message.get("more_body", False)),
            )
            if message_type == "http.disconnect":
                return

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await super().__call__(scope, receive, send)
            return

        self._trace_lifecycle(scope, "downstream.response_stream_started")

        async def send_with_trace(message: dict) -> None:
            message_type = str(message.get("type") or "")
            body = message.get("body") or b""
            started = asyncio.get_running_loop().time()
            try:
                await send(message)
            except BaseException as exc:
                self._trace_lifecycle(
                    scope,
                    "downstream.asgi_send_failed",
                    message_type=message_type,
                    body_bytes=len(body),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise
            elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000.0
            if message_type == "http.response.start" or not message.get("more_body", True):
                self._trace_lifecycle(
                    scope,
                    "downstream.asgi_send_completed",
                    message_type=message_type,
                    body_bytes=len(body),
                    more_body=bool(message.get("more_body", False)),
                    elapsed_ms=round(elapsed_ms, 3),
                )
            elif elapsed_ms >= 50.0:
                self._trace_lifecycle(
                    scope,
                    "downstream.asgi_send_slow",
                    message_type=message_type,
                    body_bytes=len(body),
                    elapsed_ms=round(elapsed_ms, 3),
                )

        stream_task = asyncio.create_task(self.stream_response(send_with_trace))
        disconnect_task = asyncio.create_task(self.listen_for_disconnect(scope, receive))
        try:
            done, _ = await asyncio.wait(
                [stream_task, disconnect_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stream_task in done:
                # Preserve normal completion and upstream handler failures.
                try:
                    stream_task.result()
                except OSError:
                    # A write may still fail before the receive-side watcher
                    # observes the disconnect; it has the same meaning.  This
                    # branch is distinct from ``http.disconnect`` because it
                    # pinpoints a failed downstream write (e.g. a proxy/client
                    # that has stopped consuming the stream).
                    self._record_downstream_abort(
                        scope,
                        reason="downstream_send_error",
                        action="body_stream_send_failed",
                    )
                    return
                self._trace_lifecycle(scope, "downstream.body_stream_completed")
            else:
                # ``http.disconnect`` won the race.  Cancelling the body
                # closes the nested pipeline and its httpx response context.
                self._record_downstream_abort(
                    scope,
                    reason="http.disconnect",
                    action="cancel_body_stream",
                )
                self._trace_lifecycle(scope, "downstream.http_disconnect_observed")
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task
        except asyncio.CancelledError:
            self._trace_lifecycle(scope, "downstream.response_task_cancelled")
            raise
        except BaseException as exc:
            self._trace_lifecycle(
                scope,
                "downstream.response_task_error",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        finally:
            if not disconnect_task.done():
                disconnect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await disconnect_task

            self._trace_lifecycle(scope, "downstream.response_stream_finalized")

        if self.background is not None:
            await self.background()


class RequestLifecycleMiddleware:
    """Attach a request id without buffering or wrapping response streams.

    ``BaseHTTPMiddleware`` (which backs FastAPI's ``@app.middleware('http')``)
    bridges response bodies through an internal memory stream.  For a long SSE
    response that bridge can hide ``http.disconnect`` from the original
    ``StreamingResponse``; its async generator then keeps the upstream HTTP
    response open after the client is gone.  A small ASGI middleware preserves
    the request-lifecycle events while passing the original ``receive`` callable
    to the streaming response unchanged.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        state = scope.setdefault("state", {})
        req_id = state.get("req_id") or headers.get("x-request-id") or uuid.uuid4().hex[:8]
        state["req_id"] = req_id

        _req_events.emit_received(
            req_id=req_id,
            client_model=headers.get("x-client-model", ""),
            stream=scope.get("method", "").upper() == "POST",
            endpoint=scope.get("path", ""),
        )

        status_code: int | None = None
        completed = False

        async def send_with_lifecycle(message: dict) -> None:
            nonlocal status_code, completed
            if message.get("type") == "http.response.start":
                status_code = int(message["status"])
            elif (
                message.get("type") == "http.response.body"
                and not message.get("more_body", False)
                and not completed
            ):
                completed = True
                _req_events.emit_completed(req_id=req_id, status=status_code or 200)
            await send(message)

        try:
            await self.app(scope, receive, send_with_lifecycle)
        except asyncio.CancelledError:
            _req_events.emit_cancelled(req_id=req_id)
            raise


app.add_middleware(RequestLifecycleMiddleware)


class RequestBodyTooLargeError(ValueError):
    """Raised when an API request exceeds the configured pre-parse limit."""


async def _read_json_request(req: Request) -> Any:
    """Read JSON incrementally so an oversized chunked body cannot cause OOM."""
    max_body_bytes = int(CONFIG["request_limits"]["max_body_bytes"])
    declared_size = req.headers.get("content-length")
    if declared_size:
        try:
            if int(declared_size) > max_body_bytes:
                raise RequestBodyTooLargeError(
                    f"Request body exceeds the {max_body_bytes}-byte limit"
                )
        except ValueError as exc:
            if isinstance(exc, RequestBodyTooLargeError):
                raise
            # An invalid Content-Length is handled by the HTTP server.  Do not
            # make this guard invent a second parsing policy for it.

            pass

    chunks: list[bytes] = []
    received_bytes = 0
    async for chunk in req.stream():
        received_bytes += len(chunk)
        if received_bytes > max_body_bytes:
            raise RequestBodyTooLargeError(
                f"Request body exceeds the {max_body_bytes}-byte limit"
            )
        chunks.append(chunk)
    return json.loads(b"".join(chunks))


def _request_body_too_large_response() -> JSONResponse:
    """Return one stable, OpenAI-style response for a rejected request body."""
    return JSONResponse(
        status_code=413,
        content={
            "error": {
                "message": "Request body exceeds the configured size limit.",
                "type": "invalid_request_error",
                "code": "request_too_large",
            }
        },
    )


# ----------------------------
# Exception Handlers
# ----------------------------
@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Custom 404 handler with logging."""
    _app.emit_not_found(path=request.url.path, dispatcher=get_event_dispatcher())
    return JSONResponse(
        status_code=404,
        content={"detail": f"Path '{request.url.path}' not found"},
    )


# ----------------------------
# System Endpoints
# ----------------------------
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/metrics")
async def get_metrics():
    """Metrics endpoint - delegates to metrics module."""
    from .metrics import METRICS_COLLECTOR

    return METRICS_COLLECTOR.get_system_metrics()


@app.get("/routes", include_in_schema=False)
async def get_routes_status():
    """Return private, dashboard-oriented state for all public configured routes."""
    from . import config as runtime_config
    from .config import resolve_route_settings
    from .routing.router import resolve_inherited_route

    registry = get_route_status_registry()
    routes_by_name = {route.name: route for route in runtime_config.USER_ROUTES}
    routes: list[dict[str, Any]] = []
    for route in sorted(runtime_config.USER_ROUTES, key=lambda item: item.name):
        if route._is_private:
            continue
        resolved = resolve_inherited_route(
            route, routes_by_name, defaults=runtime_config.DEFAULTS
        )
        ctx_len, max_tokens = resolve_route_settings(
            resolved, {}, runtime_config.DEFAULTS
        )
        dynamic_model = bool(resolved.passthrough_enabled and resolved.model_pattern)
        status = registry.snapshot(route.name) if registry is not None else {
            "activity": [],
            "errors": [],
            "pending_requests": [],
            "active_requests": [],
            "performance": {
                "samples": 0,
                "avg_prompt_tps": None,
                "avg_completion_tps": None,
                "avg_ttft_ms": None,
                "avg_elapsed_ms": None,
            },
        }
        routes.append({
            "name": route.name,
            "upstream_url": resolved.upstream_url or runtime_config.UPSTREAM_BASE_URL,
            "upstream_model": None if dynamic_model else resolved.model,
            "model_mode": "client_supplied" if dynamic_model else "configured",
            "capabilities": list(resolved.capabilities or []),
            "ctx_len": ctx_len,
            "max_tokens": max_tokens,
            **status,
        })

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_minutes": 60,
        "routes": routes,
    }


@app.get("/v1/models")
async def list_models(req: Request):
    """List available models (routes)."""
    from .auth import is_authorized
    from .config import DEFAULTS, USER_ROUTES
    from .routing import resolve_inherited_route

    models = []

    # Add configured routes (public only) as "models"
    routes_by_name = {route.name: route for route in USER_ROUTES}
    request_headers = {key: value for key, value in req.headers.items()}
    for route in sorted(USER_ROUTES, key=lambda r: r.name):
        # Skip private routes and template/base routes
        if route._is_private or route.name.startswith("base/"):
            continue

        resolved = resolve_inherited_route(route, routes_by_name, defaults=DEFAULTS)
        if not is_authorized(request_headers, resolved.api_keys or []):
            continue

        # Build config dict with resolved route details
        filters_list = None
        if isinstance(route.filters, dict):
            filters_list = list(route.filters)
        route_config = {
            "upstream_model": route.model if route.model is not None else None,
            "max_tokens": route.max_tokens if route.max_tokens is not None else None,
            "summary_enabled": (
                route.summary_enabled if route.summary_enabled is not None else False
            ),
            "passthrough_enabled": getattr(route, "passthrough_enabled", False),
            "pattern": route.pattern,
        }
        if filters_list:
            route_config["filters"] = filters_list

        ctx_len = route.ctx_len
        if ctx_len is None:
            ctx_len = DEFAULTS.ctx_len if hasattr(DEFAULTS, "ctx_len") else 4096

        models.append(
            {
                "id": route.name,
                "object": "model",
                "context_length": int(ctx_len),
                "owned_by": "orchestrator",
                "config": route_config,
            }
        )

    return {"data": models}


# ----------------------------
# API Endpoints (Delegated to Modular Handlers)
# ----------------------------
@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    """Chat completions endpoint - delegates to endpoints.chat_completions."""

    from fastapi.responses import JSONResponse

    from .endpoints.chat_completions import process_chat_request

    # Extract payload and headers for the new handler signature
    try:
        payload = await _read_json_request(req)
    except RequestBodyTooLargeError:
        return _request_body_too_large_response()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_json",
                "message": f"Request body is not valid JSON: {str(e)}",
            },
        )

    headers = {k: v for k, v in req.headers.items()}
    # Generate req_id if not present (should be added by middleware)
    req_id = getattr(req.state, "req_id", getattr(req, "req_id", "-"))

    # ``StreamingResponse`` owns the ASGI receive channel for its complete
    # lifetime and observes ``http.disconnect`` itself.  A second
    # ``Request.receive()`` consumer here races that listener, so a disconnect
    # may be consumed by the wrong task and leave the body stream alive.
    result = await process_chat_request(payload, headers, req_id)

    # Handle StreamingResponse (async iterators)
    if hasattr(result, "__aiter__"):
        return DisconnectAwareStreamingResponse(
            result,
            media_type="text/event-stream",
            headers={
                "X-Content-Type": "application/json",
                # SSE must be flushed through any compatible reverse proxy.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return result


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    """Embeddings endpoint - delegates to endpoints.embeddings."""
    from .endpoints.embeddings import embeddings_handler

    # Extract payload and headers for the new handler signature
    try:
        payload = await _read_json_request(req)
    except RequestBodyTooLargeError:
        return _request_body_too_large_response()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_json",
                "message": f"Request body is not valid JSON: {str(e)}",
            },
        )
    headers = {k: v for k, v in req.headers.items()}
    req_id = getattr(req.state, "req_id", "-")

    result = await embeddings_handler(payload, headers, req_id)
    return result
