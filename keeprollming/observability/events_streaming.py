"""Streaming event emission helpers for O7 Phase 2 migration.

Provides ``emit_streaming_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="streaming", component="handler", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    pipeline_build                      → execution.streaming.pipeline_build
    stream_handler_entry                → execution.streaming.handler_entry
    stream_handler_pipeline             → execution.streaming.handler_pipeline
    upstream_stream_connect             → execution.streaming.upstream_connect
    upstream_stream_connected           → execution.streaming.upstream_connected
    upstream_stream_closed              → execution.streaming.upstream_closed
    pipeline_run_stream_start           → execution.streaming.pipeline_run_start
    pipeline_run_stream_done            → execution.streaming.pipeline_run_done
    downstream_closed                   → execution.streaming.downstream_closed
    streaming_error                     → execution.streaming.handler_error
    downstream_complete                 → execution.streaming.downstream_complete
    stream_closed                       → execution.streaming.stream_closed

DIAGNOSTIC events retained as log() (per D-060):
    sse_keepalive                       → NOT MIGRATED (high-frequency keepalive)
    upstream_stream_chunk               → NOT MIGRATED (high-frequency per-chunk)
    pipeline_run_stream_yield           → NOT MIGRATED (high-frequency per-chunk)

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_streaming_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a streaming RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.streaming.handler_entry").
    level : str
        Log level: DEBUG, INFO, WARN, ERROR. Default "INFO".
    dispatcher : EventDispatcher | None
        Optional dispatcher. When None the call is a no-op.
    **data : Any
        Event payload fields.

    Returns
    -------
    RuntimeEvent | None
        The event that was created, or None if no dispatcher available.
    """
    source = EventSource(domain="streaming", component="handler")
    event = RuntimeEvent(
        type=event_type,
        timestamp_ns=time.time_ns(),
        source=source,
        data=data,
        req_id=req_id,
        level=level,
    )
    if dispatcher is not None:
        emit_fn = getattr(dispatcher, "emit", None)
        if emit_fn is not None:
            emit_fn(event)
    # FIX-D072: Fallback to log() removed. When dispatcher is None, the call
    # is a no-op rather than routing through legacy log(). This eliminates
    # the bypass pattern that printed JSON directly to stdout outside
    # Projector control (I-D072-01, D-072 §6).
    return event


# ── Convenience wrappers (mirror legacy log() call signatures) ──────────


def emit_pipeline_build(
    req_id: str, route_name: str = "", built: bool = False,
    filter_keys: list | None = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.pipeline_build",
                         route_name=route_name or None,
                         built=built,
                         filter_keys=filter_keys or [],
                         dispatcher=dispatcher)


def emit_handler_entry(
    req_id: str, route_name: str = "", filters: list | None = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.handler_entry",
                         route_name=route_name or None,
                         filters=filters or [], dispatcher=dispatcher)


def emit_handler_pipeline(
    req_id: str, pipeline_built: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.handler_pipeline",
                         pipeline_built=pipeline_built,
                         dispatcher=dispatcher)


def emit_stream_progress(
    req_id: str, *, elapsed_ms: float, ttft_ms: float | None, chunks: int,
    output_chars: int, output_tokens_est: int, decode_tps_est: float | None,
    dispatcher: Optional[Any] = None,
) -> None:
    """Emit rate-limited live stream telemetry (estimated until final usage)."""
    return emit_streaming_event(
        req_id, "execution.streaming.progress", level="BASIC",
        elapsed_ms=round(elapsed_ms, 1),
        ttft_ms=round(ttft_ms, 1) if ttft_ms is not None else None,
        chunks=chunks,
        output_chars=output_chars,
        output_tokens_est=output_tokens_est,
        decode_tps_est=round(decode_tps_est, 1) if decode_tps_est is not None else None,
        dispatcher=dispatcher,
    )


def emit_upstream_connect(
    req_id: str, url: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.upstream_connect",
                         level="DEBUG",
                         url=url, dispatcher=dispatcher)


def emit_upstream_connected(
    req_id: str, status: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.upstream_connected",
                         level="DEBUG",
                         status=status, dispatcher=dispatcher)


def emit_upstream_closed(
    req_id: str, reason: str, total_chunks: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.upstream_closed",
                         reason=reason, total_chunks=total_chunks,
                         dispatcher=dispatcher)


def emit_pipeline_run_start(
    req_id: str, route: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.pipeline_run_start",
                         route=route or None, dispatcher=dispatcher)


def emit_pipeline_run_done(
    req_id: str, total_yielded: int,
    execution_usage: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.pipeline_run_done",
                         total_yielded=total_yielded,
                         execution_usage=execution_usage,
                         dispatcher=dispatcher)


def emit_downstream_closed(
    req_id: str, reason: str = "", chunks_yielded: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.downstream_closed",
                         reason=reason, chunks_yielded=chunks_yielded,
                         dispatcher=dispatcher)


def emit_handler_error(
    req_id: str, error: str = "",
    route_name: Optional[str] = None,
    upstream_url: Optional[str] = None,
    upstream_model: Optional[str] = None,
    dispatcher: Optional[Any] = None,
) -> None:
    """Emit streaming handler error event.

    O11: Extended with route/upstream context so BodyCaptureConsumer can
    capture metadata when no response body is available at the failure boundary.
    """
    data: dict[str, Any] = {"error": error or None}
    if route_name is not None:
        data["route"] = route_name
    if upstream_url is not None:
        data["upstream_url"] = upstream_url
    if upstream_model is not None:
        data["upstream_model"] = upstream_model
    return emit_streaming_event(req_id, "execution.streaming.handler_error",
                         level="ERROR",
                         dispatcher=dispatcher, **data)


def emit_downstream_complete(
    req_id: str, total_yielded: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.downstream_complete",
                         total_yielded=total_yielded,
                         dispatcher=dispatcher)


def emit_stream_closed(
    req_id: str, chunks_yielded: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_event(req_id, "execution.streaming.stream_closed",
                         level="DEBUG",
                         chunks_yielded=chunks_yielded,
                         dispatcher=dispatcher)


def emit_trace_request_started(
    req_id: str, *, route: str, dispatcher: Optional[Any] = None,
) -> None:
    """Announce an opt-in request to the raw trace consumer."""
    if dispatcher is None:
        return
    dispatcher.emit(RuntimeEvent(
        type="transport.trace.request_started",
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="transport", component="trace"),
        data={"route": route}, req_id=req_id, level="TRACE",
    ))


def emit_trace_chunk(
    req_id: str, *, direction: str, boundary: str, chunk_index: int,
    raw_bytes: bytes, started_monotonic_ns: int, dispatcher: Optional[Any] = None,
) -> None:
    """Publish an exact transport chunk for the specialized raw trace sink."""
    if dispatcher is None:
        return
    now = time.perf_counter_ns()
    dispatcher.emit(RuntimeEvent(
        type="transport.trace.chunk",
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="transport", component="trace"),
        data={
            "direction": direction,
            "boundary": boundary,
            "chunk_index": chunk_index,
            "monotonic_ns": now,
            "relative_ns": now - started_monotonic_ns,
            "raw_bytes": raw_bytes,
        },
        req_id=req_id, level="TRACE",
    ))
