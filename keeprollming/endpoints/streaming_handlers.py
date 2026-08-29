"""Streaming request processing utilities for chat completions.

This module contains the streaming-specific logic for handling SSE responses
and delegating to the filter chain.
"""

import asyncio
import contextlib
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Set

import httpx

from ..logger import log
from ..routing import Route
from ..streaming.tool_call_handler import ToolCallAccumulator
from ..upstream import make_request_timeout
from ..utils.token_utils import count_text_tokens_safe

# Lazy import to avoid circular dependency
try:
    from ..observability.events import EventSource, RuntimeEvent

    _OBSERVABILITY_AVAILABLE = True
except ImportError:
    _OBSERVABILITY_AVAILABLE = False

# O7 Phase 2: streaming event emission helpers
from ..observability.events_streaming import (
    emit_downstream_closed,
    emit_downstream_complete,
    emit_handler_entry,
    emit_handler_error,
    emit_handler_pipeline,
    emit_pipeline_build,
    emit_pipeline_run_done,
    emit_pipeline_run_start,
    emit_stream_closed,
    emit_stream_progress,
    emit_trace_chunk,
    emit_trace_lifecycle,
    emit_trace_request_started,
    emit_upstream_closed,
    emit_upstream_connect,
    emit_upstream_connected,
)

# The V2 pipeline is the only streaming execution path.
try:
    from ..orchestrator.pipeline import Pipeline

    PIPELINE_AVAILABLE = True
except ImportError:
    PIPELINE_AVAILABLE = False

# Keepalive interval (seconds) — configurable for debugging.
# Set to 0 or None to disable keepalive entirely.
_SSE_KEEPALIVE_INTERVAL: float = 15.0

# An estimated decode rate from a handful of characters in a sub-millisecond
# interval is noise, not telemetry.  Keep the first progress event for TTFT,
# but wait for a minimally representative decode sample before publishing TPS.
_MIN_DECODE_TOKENS_ESTIMATE = 8
_MIN_DECODE_WINDOW_SECONDS = 0.1


class _StreamTranscript:
    """Accumulate the client-visible semantic result of a streamed response."""

    def __init__(self) -> None:
        self._buffer = ""
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls = ToolCallAccumulator()
        self.finish_reason: str | None = None
        self.usage: dict | None = None

    def consume(self, chunk: bytes) -> None:
        self._buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in self._buffer:
            block, self._buffer = self._buffer.split("\n\n", 1)
            payload = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            ).strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
                choice = event.get("choices", [{}])[0]
                delta = choice.get("delta", {}) or {}
            except (ValueError, AttributeError, IndexError, TypeError):
                continue
            if isinstance(event.get("usage"), dict):
                self.usage = event["usage"]
            if isinstance(delta.get("content"), str):
                self.content.append(delta["content"])
            if isinstance(delta.get("reasoning_content"), str):
                self.reasoning.append(delta["reasoning_content"])
            self.tool_calls.add_delta(delta)
            if isinstance(choice.get("finish_reason"), str):
                self.finish_reason = choice["finish_reason"]

    def final_tool_calls(self) -> list[dict]:
        return [
            self.tool_calls.build_final_calls(index)
            for index in sorted(self.tool_calls.accumulators)
        ]

    def client_visible_completion_tokens(self) -> int | None:
        """Estimate tokens in the semantic result delivered to the client.

        Recovery attempts may be discarded or merged by finalizers, so their
        upstream usage cannot represent client-visible decode throughput.
        Count the final SSE transcript instead, including reasoning and the
        completed structured tool calls that the client receives.
        """
        parts = [*self.content, *self.reasoning]
        tool_calls = self.final_tool_calls()
        if tool_calls:
            parts.append(json.dumps(tool_calls, ensure_ascii=False, separators=(",", ":")))
        return count_text_tokens_safe("\n".join(parts))


class _StreamProgress:
    """Rate-limited live telemetry derived from upstream token deltas.

    Finalizers may intentionally buffer or merge downstream SSE output.  Decode
    timing must therefore be observed before the V2 pipeline, not from the
    client-visible chunks it eventually emits.
    """

    def __init__(self, started_at: float) -> None:
        from ..core.config_loader import LOG_STREAM_PROGRESS_INTERVAL_MS

        self._started_at = started_at
        self._interval_s = LOG_STREAM_PROGRESS_INTERVAL_MS / 1000.0
        self._last_emitted_at = started_at
        self._first_output_at: float | None = None
        self._output_chars = 0
        self._upstream_buffer = ""
        self.chunk_count = 0

    def observe_upstream(self, chunk: bytes) -> None:
        """Count all generated semantic output in raw upstream SSE frames."""
        self.chunk_count += 1
        self._upstream_buffer += chunk.decode("utf-8", errors="replace")
        while "\n\n" in self._upstream_buffer:
            block, self._upstream_buffer = self._upstream_buffer.split("\n\n", 1)
            payload = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            ).strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
                choices = event.get("choices", [])
            except (TypeError, ValueError):
                continue
            for choice in choices:
                delta = choice.get("delta", {}) or {}
                generated_chars = 0
                for field in ("content", "reasoning_content"):
                    value = delta.get(field)
                    if isinstance(value, str):
                        generated_chars += len(value)
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict):
                            continue
                        function = tool_call.get("function", {})
                        if isinstance(function, dict):
                            for field in ("name", "arguments"):
                                value = function.get(field)
                                if isinstance(value, str):
                                    generated_chars += len(value)
                if generated_chars:
                    if self._first_output_at is None:
                        self._first_output_at = time.perf_counter()
                    self._output_chars += generated_chars

    def emit_if_due(self, req_id: str, *, dispatcher=None, force: bool = False) -> None:
        if self._interval_s <= 0 or self._output_chars == 0:
            return
        now = time.perf_counter()
        if not force and now - self._last_emitted_at < self._interval_s:
            return
        elapsed_ms = (now - self._started_at) * 1000.0
        ttft_ms = (
            (self._first_output_at - self._started_at) * 1000.0
            if self._first_output_at is not None
            else None
        )
        decode_started_at = self._first_output_at if self._first_output_at is not None else now
        decode_seconds = max(now - decode_started_at, 0.001)
        # OpenAI-compatible SSE does not carry incremental token counts.  This
        # estimate is explicitly labelled; final usage remains authoritative.
        output_tokens_est = max(1, round(self._output_chars / 4))
        decode_tps_est = None
        if (
            output_tokens_est >= _MIN_DECODE_TOKENS_ESTIMATE
            and decode_seconds >= _MIN_DECODE_WINDOW_SECONDS
        ):
            decode_tps_est = output_tokens_est / decode_seconds

        emit_stream_progress(
            req_id=req_id,
            elapsed_ms=elapsed_ms,
            ttft_ms=ttft_ms,
            chunks=self.chunk_count,
            output_chars=self._output_chars,
            output_tokens_est=output_tokens_est,
            decode_tps_est=decode_tps_est,
            dispatcher=dispatcher,
        )
        self._last_emitted_at = now

    @property
    def ttft_ms(self) -> float | None:
        """Measured upstream time to first textual token, if one arrived."""
        if self._first_output_at is None:
            return None
        return (self._first_output_at - self._started_at) * 1000.0


async def _interleave_keepalive(
    gen: AsyncIterator[bytes],
    interval: float | None = None,
) -> AsyncIterator[bytes]:
    """Interleave SSE keepalive markers between long gaps in upstream generator.

    Covers ALL phases: upstream loading, streaming, retry, post-processing.
    The keepalive is emitted whenever ``interval`` seconds pass without a chunk
    from the upstream generator.

    Unlike ``asyncio.wait_for(anext, timeout)``, this implementation does NOT
    cancel the pending ``__anext__()`` call when the timeout fires — otherwise
    the ``CancelledError`` propagates through the generator chain and kills the
    entire pipeline mid-stream (the "keepkilling" bug).

    If interval is 0 or None, keepalive is disabled (pass-through).
    """
    it = gen.__aiter__()
    effective_interval = interval if interval is not None else _SSE_KEEPALIVE_INTERVAL
    if effective_interval is None or effective_interval <= 0:
        # Keepalive disabled — forward chunks directly
        async for chunk in gen:
            yield chunk
        return

    # Reusable task that reads the next chunk from the generator.  Timer
    # expiry must not cancel it: that would terminate a healthy but slow
    # upstream stream.  The wrapper owns this task and cancels it if the ASGI
    # server closes the body generator after a downstream disconnect.
    next_task = asyncio.ensure_future(it.__anext__())
    sleep_task: asyncio.Task | None = None
    try:
        while True:
            sleep_task = asyncio.ensure_future(asyncio.sleep(effective_interval))
            try:
                done, _ = await asyncio.wait(
                    [next_task, sleep_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if next_task in done:
                    # A chunk arrived (or the generator finished)
                    try:
                        chunk = next_task.result()
                    except StopAsyncIteration:
                        return
                    yield chunk
                    # Do not read ahead while the downstream ASGI send is
                    # suspended.  This preserves backpressure and makes each
                    # received-upstream chunk causally follow its prior
                    # downstream handoff.
                    next_task = asyncio.ensure_future(it.__anext__())
                else:
                    # Timeout — no chunk yet; keep the reader alive.
                    log("DEBUG", "sse_keepalive")
                    yield b": keepalive\n\n"
            finally:
                if sleep_task is not None and not sleep_task.done():
                    sleep_task.cancel()
    finally:
        # A client disconnect closes this wrapper while ``next_task`` can be
        # blocked in httpx's body reader.  Leaving it alive retains the
        # upstream socket (and its unread receive buffer) after the ASGI
        # request is gone.  This is intentionally separate from the timer
        # path above: only lifecycle termination cancels the reader.
        if not next_task.done():
            next_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await next_task
        # When the downstream disconnect happens while this generator is
        # paused at ``yield chunk``, ``next_task`` has already completed and
        # cancellation alone cannot close the nested pipeline iterator.  Close
        # it explicitly so its ``async with client.stream(...)`` unwinds and
        # the upstream server observes the client disconnect.
        close = getattr(it, "aclose", None)
        if close is not None:
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await close()


def _build_pipeline(route, req_id=None, dispatcher=None):
    """Build the V2 Pipeline for every resolved streaming route.

    Parameters
    ----------
    route:
        The Route object with canonical filters configuration.
    req_id:
        Optional request ID for pipeline_build event correlation.
    dispatcher:
        Optional EventDispatcher for pipeline_build event emission.
    """
    if not PIPELINE_AVAILABLE:
        raise RuntimeError("V2 streaming pipeline is unavailable")
    api_key = getattr(route, "api_key", None)
    filters = getattr(route, "filters", None)
    p = Pipeline.from_route_config(filters, api_key=api_key) or Pipeline()
    emit_pipeline_build(
        req_id=req_id,
        route_name=getattr(route, "name", "?"),
        built=p is not None,
        filter_keys=list(filters.keys()) if filters else [],
        dispatcher=dispatcher,
    )
    return p


def _strip_last_image_url(messages: list) -> Optional[int]:
    """Remove the LAST image_url item from messages, replace with placeholder.

    Returns the number of image_url items stripped (0 or 1), or None if no
    image found.
    """
    for msg in reversed(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for idx in range(len(content) - 1, -1, -1):
            item = content[idx]
            if isinstance(item, dict) and item.get("type") == "image_url":
                content[idx] = {
                    "type": "text",
                    "text": "[Image omitted due to error]",
                }
                return 1
    return 0


async def process_streaming_request(
    url: str,
    client: httpx.AsyncClient,
    payload: Dict[str, Any],
    route_headers: Dict[str, str],
    route: Route,
    req_id: str,
    request_timeout: float,
    fallback_attempts: List[Dict[str, str]],
    visited_models: Set[str],
    upstream_model: str,
    is_passthrough: bool,
    transform_reasoning_content: bool,
    add_empty_content_when_reasoning_only: bool,
    reasoning_placeholder: str,
    t_start: float,
    record_metrics_func: Any = None,  # O10: deprecated, kept for backward compatibility
    dispatcher: Any = None,
    pipeline: Any = None,
    enabled_filters: Sequence[str] | None = None,
) -> AsyncIterator[bytes]:
    """Process a streaming chat completion request via filter chain.

    This is a thin driver that delegates all filtering logic to the configured
    filter chain. The handler itself contains no RLS/TLS/nudge/tool_rewrite logic.

    O10: Performance metrics are now emitted as RuntimeEvents instead of
    calling record_metrics_func(). The parameter is retained for backward
    compatibility but ignored.

    Parameters
    ----------
    record_metrics_func:
        Deprecated (O10). Retained for backward compatibility with tests.
        Performance events are now emitted directly via EventDispatcher.
    dispatcher:
        Optional EventDispatcher for observability instrumentation.

    Yields:
        Streaming chunks (bytes), including final [DONE] marker
    """
    emit_handler_entry(
        req_id=req_id,
        route_name=getattr(route, "name", "?"),
        filters=list(enabled_filters)
        if enabled_filters is not None
        else Pipeline.enabled_filter_names(getattr(route, "filters", None)),
        dispatcher=dispatcher,
    )

    # O2: emit handler entry event
    if _OBSERVABILITY_AVAILABLE and dispatcher is not None:
        dispatcher.emit(
            RuntimeEvent(
                type="streaming.handler.entry",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="streaming", component="handler"),
                data={
                    "route": route.name if hasattr(route, "name") else "?",
                    "stream": True,
                },
                req_id=req_id,
                level="INFO",
            )
        )

    t0 = time.perf_counter()
    trace_started_ns = time.perf_counter_ns()
    emit_trace_request_started(
        req_id,
        route=getattr(route, "name", ""),
        dispatcher=dispatcher,
    )
    progress = _StreamProgress(t0)
    if pipeline is None:
        pipeline = _build_pipeline(route, req_id=req_id, dispatcher=dispatcher)
    else:
        filters = getattr(route, "filters", None)
        emit_pipeline_build(
            req_id=req_id,
            route_name=getattr(route, "name", "?"),
            built=True,
            filter_keys=list(filters.keys()) if isinstance(filters, dict) else [],
            dispatcher=dispatcher,
        )
    emit_handler_pipeline(
        req_id=req_id,
        pipeline_built=pipeline is not None,
        dispatcher=dispatcher,
    )

    # Build upstream async generator
    async def upstream_stream(upstream_payload):
        emit_upstream_connect(
            req_id=req_id,
            url=url[:80],
            dispatcher=dispatcher,
        )
        chunk_count = 0
        close_reason = "unknown"
        connected_at: float | None = None
        first_chunk_seen = False
        try:
            emit_trace_lifecycle(
                req_id,
                boundary="upstream.connect_started",
                dispatcher=dispatcher,
                url=url[:200],
                method="POST",
            )
            async with client.stream(
                "POST",
                url,
                json=upstream_payload,
                headers=route_headers,
                timeout=make_request_timeout(request_timeout),
            ) as resp:
                connected_at = time.perf_counter()
                # Exact socket metadata is optional diagnostics. Lightweight
                # response doubles and non-httpx transports need not expose
                # the httpx extensions mapping.
                response_extensions = getattr(resp, "extensions", {}) or {}
                network_stream = response_extensions.get("network_stream")
                get_extra_info = getattr(network_stream, "get_extra_info", None)
                peer = get_extra_info("peername") if callable(get_extra_info) else None
                sockname = get_extra_info("sockname") if callable(get_extra_info) else None
                emit_trace_lifecycle(
                    req_id,
                    boundary="upstream.response_headers",
                    dispatcher=dispatcher,
                    status=resp.status_code,
                    peer=str(peer) if peer is not None else None,
                    local_socket=str(sockname) if sockname is not None else None,
                    content_type=resp.headers.get("content-type"),
                )
                emit_upstream_connected(
                    req_id=req_id,
                    status=resp.status_code,
                    dispatcher=dispatcher,
                )
                async for chunk in resp.aiter_bytes():
                    chunk_count += 1
                    if not first_chunk_seen:
                        first_chunk_seen = True
                        emit_trace_lifecycle(
                            req_id,
                            boundary="upstream.first_chunk",
                            dispatcher=dispatcher,
                            chunk_bytes=len(chunk),
                            after_headers_ms=round((time.perf_counter() - connected_at) * 1000.0, 3)
                            if connected_at is not None
                            else None,
                        )
                    emit_trace_chunk(
                        req_id,
                        direction="upstream",
                        boundary="upstream.received",
                        chunk_index=chunk_count,
                        raw_bytes=chunk,
                        started_monotonic_ns=trace_started_ns,
                        dispatcher=dispatcher,
                    )
                    progress.observe_upstream(chunk)
                    progress.emit_if_due(req_id, dispatcher=dispatcher)
                    yield chunk
                close_reason = "normal"
        except GeneratorExit:
            close_reason = "generator_exit"
            raise
        except asyncio.CancelledError:
            close_reason = "cancelled"
            raise
        except Exception as e:
            close_reason = f"error:{type(e).__name__}"
            emit_trace_lifecycle(
                req_id,
                boundary="upstream.stream_error",
                dispatcher=dispatcher,
                error_type=type(e).__name__,
                error=str(e),
                chunk_count=chunk_count,
            )
            raise
        finally:
            emit_trace_lifecycle(
                req_id,
                boundary="upstream.stream_closed",
                dispatcher=dispatcher,
                reason=close_reason,
                chunk_count=chunk_count,
            )
            emit_upstream_closed(
                req_id=req_id,
                reason=close_reason,
                total_chunks=chunk_count,
                dispatcher=dispatcher,
            )

    # Every streaming request passes through the V2 pipeline, including routes
    # with no configured filters (which use Pipeline([]) and core finalizers).
    # Get ExecutionUsage for metrics after the V2 runner completes.
    _execution_usage = None

    emit_pipeline_run_start(
        req_id=req_id,
        route=getattr(route, "name", ""),
        dispatcher=dispatcher,
    )
    chunk_count = 0
    transcript = _StreamTranscript()

    try:
        _pipeline_stream = pipeline.run_stream(
            payload,
            req_id,
            upstream_model,
            route_name=getattr(route, "name", ""),
            upstream_url=str(getattr(route, "upstream_url", "")),
            upstream_stream=upstream_stream,
            dispatcher=dispatcher,
        )

        async for chunk in _interleave_keepalive(_pipeline_stream):
            chunk_count += 1
            emit_trace_chunk(
                req_id,
                direction="downstream",
                boundary="pipeline.output",
                chunk_index=chunk_count,
                raw_bytes=chunk,
                started_monotonic_ns=trace_started_ns,
                dispatcher=dispatcher,
            )
            transcript.consume(chunk)
            yield chunk

        progress.emit_if_due(req_id, dispatcher=dispatcher, force=True)

        # Capture ExecutionUsage after iteration for metrics.
        if hasattr(pipeline, "_execution_usage"):
            _execution_usage = pipeline._execution_usage

        emit_pipeline_run_done(
            req_id=req_id,
            total_yielded=chunk_count,
            execution_usage=_execution_usage is not None,
            dispatcher=dispatcher,
        )
    except asyncio.CancelledError:
        # ASGI cancels this generator when the downstream connection closes.
        # This must remain cancellation (rather than an SSE error response),
        # but it is an essential diagnostic boundary: the upstream stream is
        # about to be closed without a terminal OpenAI frame.
        emit_downstream_closed(
            req_id=req_id,
            reason="cancelled",
            chunks_yielded=chunk_count,
            dispatcher=dispatcher,
        )
        raise
    except GeneratorExit:
        emit_downstream_closed(
            req_id=req_id,
            reason="client_disconnect",
            chunks_yielded=chunk_count,
            dispatcher=dispatcher,
        )
        return
    except Exception as e:
        # O11: include route/upstream context for BodyCaptureConsumer metadata capture
        emit_handler_error(
            req_id=req_id,
            error=str(e) or type(e).__name__,
            route_name=getattr(route, "name", "?"),
            upstream_url=url[:200] if url else None,
            upstream_model=upstream_model,
            dispatcher=dispatcher,
        )
        yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n".encode("utf-8")
        # Yield finish_reason before [DONE] even on error
        stop_chunk = json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield f"data: {stop_chunk}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"
        # O10-NF02: emit performance metrics on error path for parity with non-streaming
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        from ..observability import events_execution as _exec_perf

        _exec_perf.emit_performance_request_complete(
            req_id=req_id,
            model=upstream_model,
            route_name=getattr(route, "name", "?"),
            route_hierarchy=getattr(route, "_route_hierarchy", [getattr(route, "name", "?")]),
            stream=True,
            elapsed_ms=elapsed_ms,
            ttft_ms=progress.ttft_ms,
            completion_tokens=None,
            prompt_tokens=None,
            total_tokens=None,
            finish_reason="error",
            did_summarize=False,
            passthrough=is_passthrough,
            completion_tokens_source="missing",
            dispatcher=dispatcher,
        )
        _exec_perf.emit_derived_performance_metrics(
            req_id,
            elapsed_ms=elapsed_ms,
            completion_tokens=None,
            ttft_ms=progress.ttft_ms,
            prompt_tokens=None,
            total_tokens=None,
            model=upstream_model,
            route_name=getattr(route, "name", "?"),
            completion_tokens_source="missing",
            dispatcher=dispatcher,
        )
        return
    emit_downstream_complete(
        req_id=req_id,
        total_yielded=chunk_count,
        dispatcher=dispatcher,
    )
    emit_stream_closed(
        req_id=req_id,
        chunks_yielded=chunk_count,
        dispatcher=dispatcher,
    )

    # The pipeline and direct-upstream paths both expose one semantic result.
    # Emit it once, after all client-visible SSE frames have been observed.
    if transcript is not None:
        from ..observability import events_execution as _exec

        assistant_text = "".join(transcript.content)
        reasoning_text = "".join(transcript.reasoning)
        _exec.emit_assistant(
            req_id,
            assistant_text,
            len(assistant_text),
            tool_calls=transcript.final_tool_calls() or None,
            reasoning_content=reasoning_text,
            reasoning_length=len(reasoning_text),
            finish_reason=transcript.finish_reason,
            dispatcher=dispatcher,
        )

    # O10: Emit performance event instead of calling record_metrics_func()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Prefer an explicit dispatcher; the app normally supplies the global
    # singleton, while component callers need isolated event capture.
    from ..app import get_event_dispatcher

    event_dispatcher = dispatcher if dispatcher is not None else get_event_dispatcher()

    performance_completion_tokens = None
    performance_prompt_tokens = None
    performance_total_tokens = None
    performance_cached_prompt_tokens = None
    performance_completion_tokens_source = "missing"

    # Use ExecutionUsage if available (Phase 1 internal accounting)
    if _execution_usage is not None:
        from ..observability import events_execution as _exec_perf

        logical_prompt_tokens = (
            _execution_usage.final_prompt_tokens
            if _execution_usage.usage_reported_attempts > 0
            else None
        )
        logical_completion_tokens = transcript.client_visible_completion_tokens()
        _execution_usage.set_final_usage(
            prompt_tokens=logical_prompt_tokens,
            completion_tokens=logical_completion_tokens,
        )
        logical_total_tokens = (
            logical_prompt_tokens + logical_completion_tokens
            if logical_prompt_tokens is not None and logical_completion_tokens is not None
            else None
        )
        performance_completion_tokens = logical_completion_tokens
        performance_prompt_tokens = logical_prompt_tokens
        performance_total_tokens = logical_total_tokens
        performance_cached_prompt_tokens = getattr(
            _execution_usage, "final_cached_prompt_tokens", None
        )
        performance_completion_tokens_source = "client_visible_estimate"
        _exec_perf.emit_performance_request_complete(
            req_id=req_id,
            model=upstream_model,
            route_name=getattr(route, "name", "?"),
            route_hierarchy=getattr(route, "_route_hierarchy", [getattr(route, "name", "?")]),
            stream=True,
            elapsed_ms=elapsed_ms,
            ttft_ms=progress.ttft_ms,
            completion_tokens=logical_completion_tokens,
            prompt_tokens=logical_prompt_tokens,
            total_tokens=logical_total_tokens,
            finish_reason=_execution_usage.finish_reason,
            did_summarize=False,
            passthrough=is_passthrough,
            completion_tokens_source="client_visible_estimate",
            upstream_attempts=_execution_usage.upstream_attempts,
            usage_reported_attempts=_execution_usage.usage_reported_attempts,
            recovery_count=_execution_usage.recovery_count,
            retry_amplification_ratio=(
                _execution_usage.retry_amplification_ratio
                if _execution_usage.usage_reported_attempts > 0
                else None
            ),
            usage_complete=_execution_usage.usage_complete,
            upstream_prompt_tokens=_execution_usage.upstream_prompt_tokens,
            upstream_completion_tokens=_execution_usage.upstream_completion_tokens,
            upstream_total_tokens=_execution_usage.upstream_total_tokens,
            cached_prompt_tokens=performance_cached_prompt_tokens,
            dispatcher=event_dispatcher,
        )
        # I-O10-BC-01: fallback when dispatcher unavailable AND legacy callback provided
        if event_dispatcher is None and record_metrics_func is not None:
            record_metrics_func(
                {
                    "model": upstream_model,
                    "req_id": req_id,
                    "stream": True,
                    "elapsed_ms": elapsed_ms,
                    "ttft_ms": None,
                    "tps": None,
                    "total_tps": None,
                    "completion_tokens": logical_completion_tokens,
                    "prompt_tokens": logical_prompt_tokens,
                    "total_tokens": logical_total_tokens,
                    "finish_reason": None,
                    "passthrough": is_passthrough,
                    "did_summarize": False,
                    "completion_tokens_source": "client_visible_estimate",
                    "upstream_attempts": _execution_usage.upstream_attempts,
                    "usage_reported_attempts": _execution_usage.usage_reported_attempts,
                    "usage_complete": _execution_usage.usage_complete,
                    "upstream_prompt_tokens": _execution_usage.upstream_prompt_tokens,
                    "upstream_completion_tokens": _execution_usage.upstream_completion_tokens,
                    "upstream_total_tokens": _execution_usage.upstream_total_tokens,
                    "cached_prompt_tokens": performance_cached_prompt_tokens,
                }
            )
    else:
        from ..observability import events_execution as _exec_perf

        _exec_perf.emit_performance_request_complete(
            req_id=req_id,
            model=upstream_model,
            route_name=getattr(route, "name", "?"),
            route_hierarchy=getattr(route, "_route_hierarchy", [getattr(route, "name", "?")]),
            stream=True,
            elapsed_ms=elapsed_ms,
            ttft_ms=progress.ttft_ms,
            completion_tokens=None,
            prompt_tokens=None,
            total_tokens=None,
            finish_reason=None,
            did_summarize=False,
            passthrough=is_passthrough,
            completion_tokens_source="missing",
            dispatcher=event_dispatcher,
        )
        # I-O10-BC-01: fallback when dispatcher unavailable AND legacy callback provided
        if event_dispatcher is None and record_metrics_func is not None:
            record_metrics_func(
                {
                    "model": upstream_model,
                    "req_id": req_id,
                    "stream": True,
                    "elapsed_ms": elapsed_ms,
                    "ttft_ms": None,
                    "tps": None,
                    "total_tps": None,
                    "completion_tokens": None,
                    "prompt_tokens": None,
                    "total_tokens": None,
                    "finish_reason": None,
                    "passthrough": is_passthrough,
                    "did_summarize": False,
                    "completion_tokens_source": "missing",
                }
            )

    _exec_perf.emit_derived_performance_metrics(
        req_id,
        elapsed_ms=elapsed_ms,
        completion_tokens=performance_completion_tokens,
        ttft_ms=progress.ttft_ms,
        prompt_tokens=performance_prompt_tokens,
        total_tokens=performance_total_tokens,
        cached_prompt_tokens=performance_cached_prompt_tokens,
        model=upstream_model,
        route_name=getattr(route, "name", "?"),
        completion_tokens_source=performance_completion_tokens_source,
        dispatcher=event_dispatcher,
    )

    # O2: emit handler exit event
    if _OBSERVABILITY_AVAILABLE and dispatcher is not None:
        dispatcher.emit(
            RuntimeEvent(
                type="streaming.handler.exit",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="streaming", component="handler"),
                data={
                    "elapsed_ms": round(elapsed_ms, 2),
                },
                req_id=req_id,
                level="INFO",
            )
        )
