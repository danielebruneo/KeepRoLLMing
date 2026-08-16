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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from .config import (
    CONFIG,
    DEFAULTS,
    resolve_route,
    resolve_fallback_chain,
    get_route_settings,
)
from .logger import log
from .observability import events_app as _app
from .observability import events_request as _req_events


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
            _app.emit_config_reload_failed(
                error=str(e), dispatcher=_event_dispatcher
            )

        # Check every 2 seconds
        await asyncio.sleep(2)


# ── Global EventDispatcher (O10) ──────────────────────────────────

_event_dispatcher: Optional[Any] = None


def get_event_dispatcher():
    """Return the global EventDispatcher singleton."""
    return _event_dispatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks."""
    from .logger import log
    from .performance import set_performance_logs_dir, set_summary_interval
    from .async_log_writer import get_async_writer, start_async_writer, stop_async_writer

    # Start the async log writer (replaces sync flush() in hot path)
    await start_async_writer()
    # Register debug sink for streaming handler
    get_async_writer().register_sink("stream_debug", "/tmp/streaming_final_chunk.log")

    # Configure performance logs directory from config
    perf_logs_dir = os.environ.get("PERFORMANCE_LOGS_DIR") or CONFIG.get(
        "defaults", {}
    ).get("performance_logs_dir", "__performance_logs")
    set_performance_logs_dir(perf_logs_dir)

    # Configure summary update interval (reduce I/O overhead)
    summary_interval = CONFIG.get("performance", {}).get("summary_update_interval", 100)
    set_summary_interval(summary_interval)

    # ── O10: Initialize global EventDispatcher + PerformanceConsumer ──
    global _event_dispatcher
    from .observability.dispatcher import EventDispatcher
    from .observability.consumers import PerformanceConsumer

    _event_dispatcher = EventDispatcher()

    # Register PerformanceConsumer for execution.performance.* namespace
    perf_consumer = PerformanceConsumer(
        perf_logs_dir=perf_logs_dir,
        summary_interval=summary_interval,
    )
    _event_dispatcher.subscribe("execution.performance", perf_consumer)

    # FIX-D072: Wire dispatcher through upstream_client so emit_* calls flow
    # through the Projector architecture instead of bypassing to legacy log().
    from .upstream.upstream_client import set_upstream_dispatcher
    set_upstream_dispatcher(_event_dispatcher)

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
    from .observability.default_projectors import activate_default_projectors, create_default_projectors
    default_projectors = create_default_projectors(log_dir, observability_config.get("projectors"))
    activate_default_projectors(default_projectors, _event_dispatcher)

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

    # Start the config watcher background task
    asyncio.create_task(_config_watcher())

    _app.emit_starting(message="Starting...", dispatcher=_event_dispatcher)
    try:
        yield
    finally:
        _app.emit_stopping(
            message="Shutting down...",
            dispatcher=_event_dispatcher,
        )
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


# ----------------------------
# Request Middleware
# ----------------------------
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate unique request ID and attach to request state."""
    # Generate req_id if not present (e.g., from X-Request-ID header)
    req_id = getattr(request.state, 'req_id', None)
    if not req_id:
        # Check for header first
        req_id = request.headers.get("x-request-id")
    if not req_id:
        # Generate unique ID (8 chars)
        req_id = uuid.uuid4().hex[:8]

    # Attach to request state for downstream access
    request.state.req_id = req_id

    # Emit request.lifecycle.received event
    _req_events.emit_received(
        req_id=req_id,
        client_model=request.headers.get("x-client-model", ""),
        stream=request.method.upper() == "POST",
        endpoint=request.url.path,
    )

    response = await call_next(request)

    # Emit request.lifecycle.completed event
    _req_events.emit_completed(
        req_id=req_id,
        status=response.status_code,
    )

    return response


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


@app.get("/v1/models")
async def list_models():
    """List available models (routes)."""
    from .config import USER_ROUTES, DEFAULTS
    

    models = []

    # Add configured routes (public only) as "models"
    for route in sorted(USER_ROUTES, key=lambda r: r.name):
        # Skip private routes and template/base routes
        if route._is_private or route.name.startswith("base/"):
            continue

        # Build config dict with resolved route details
        filters_list = None
        if isinstance(route.filters, dict):
            filters_list = list(route.filters)
        route_config = {
            "upstream_model": route.model if route.model is not None else None,
            "max_tokens": route.max_tokens if route.max_tokens is not None else None,
            "summary_enabled": route.summary_enabled if route.summary_enabled is not None else False,
            "passthrough_enabled": getattr(route, 'passthrough_enabled', False),
            "pattern": route.pattern,
        }
        if filters_list:
            route_config["filters"] = filters_list

        ctx_len = route.ctx_len
        if ctx_len is None:
            ctx_len = DEFAULTS.ctx_len if hasattr(DEFAULTS, 'ctx_len') else 4096

        models.append({
            "id": route.name,
            "object": "model",
            "context_length": int(ctx_len),
            "owned_by": "orchestrator",
            "config": route_config,
        })

    return {"data": models}


# ----------------------------
# API Endpoints (Delegated to Modular Handlers)
# ----------------------------
@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    """Chat completions endpoint - delegates to endpoints.chat_completions."""

    from .endpoints.chat_completions import process_chat_request
    from fastapi.responses import JSONResponse

    # Extract payload and headers for the new handler signature
    try:
        payload = await req.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": f"Request body is not valid JSON: {str(e)}"}
        )
    
    headers = {k: v for k, v in req.headers.items()}
    # Generate req_id if not present (should be added by middleware)
    req_id = getattr(req.state, 'req_id', getattr(req, 'req_id', '-'))

    result = await process_chat_request(payload, headers, req_id)

    # Handle StreamingResponse (async iterators)
    if hasattr(result, '__aiter__'):
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={"X-Content-Type": "application/json"}
        )

    return result


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    """Embeddings endpoint - delegates to endpoints.embeddings."""
    from .endpoints.embeddings import embeddings_handler
    
    # Extract payload and headers for the new handler signature
    payload = await req.json()
    headers = {k: v for k, v in req.headers.items()}
    req_id = getattr(req.state, 'req_id', '-')
    
    result = await embeddings_handler(payload, headers, req_id)
    return result
