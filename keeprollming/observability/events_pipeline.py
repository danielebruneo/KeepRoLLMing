"""Pipeline event emission helpers for O7 migration.

Provides ``emit_pipeline_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="pipeline", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call falls back to ``log()``
for the configurable PLAIN and JSON projectors.

Event type mapping (legacy log() → RuntimeEvent.type):

    pipeline_process_request_error        → execution.pipeline.process_request_error
    filter_triggered_{action}             → execution.pipeline.filter_triggered
    pipeline_process_response_error       → execution.pipeline.process_response_error
    pipeline_stream_first_chunk           → execution.pipeline.stream_first_chunk
    pipeline_stream_chunk_progress        → execution.pipeline.stream_chunk_progress
    pipeline_stream_filter_error          → execution.pipeline.stream_filter_error
    pipeline_stream_retry                 → execution.pipeline.stream_retry
    pipeline_stream_stop                  → execution.pipeline.stream_stop
    pipeline_stream_exhausted             → execution.pipeline.stream_exhausted
    pipeline_buffer_orphaned              → execution.pipeline.buffer_orphaned
    pipeline_run_stream_entry          → execution.pipeline.stream_started
    pipeline_v2_phase1_done               → execution.pipeline.stream_request_filters_done
    pipeline_v2_finalizers_built          → execution.pipeline.stream_finalizers_built
    pipeline_run_stream_done           → execution.pipeline.stream_completed
    pipeline_run_stream_entry             → execution.pipeline.run_stream_entry
    pipeline_phase1_done                  → execution.pipeline.phase1_done
    pipeline_phase2_start                 → execution.pipeline.phase2_start
    pipeline_phase2_end                   → execution.pipeline.phase2_end
    pipeline_no_upstream                  → execution.pipeline.no_upstream
    pipeline_phase3_retry_start           → execution.pipeline.phase3_retry_start
    pipeline_phase3_retry_result          → execution.pipeline.phase3_retry_result
    pipeline_phase3_retry_end             → execution.pipeline.phase3_retry_end
    pipeline_phase4_yield_content         → execution.pipeline.phase4_yield_content
    pipeline_phase4_skip_tc_upstream_sent → execution.pipeline.phase4_skip_tc
    pipeline_phase4_yield_tc              → execution.pipeline.phase4_yield_tc
    pipeline_phase4_error                 → execution.pipeline.phase4_error
    pipeline_phase4_fr_stop               → execution.pipeline.phase4_fr_stop

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_pipeline_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a pipeline RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.pipeline.process_request_error").
    level : str
        Log level: DEBUG, INFO, WARN, ERROR. Default "INFO".
    dispatcher : EventDispatcher | None
        Optional dispatcher. When None falls back to log().
    **data : Any
        Event payload fields.

    Returns
    -------
    RuntimeEvent | None
        The event that was created, or None if no dispatcher available.
    """
    source = EventSource(domain="execution", component="pipeline")
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


def emit_process_request_error(
    req_id: str, filter_name: str, error: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.process_request_error",
                         level="ERROR",
                         filter_name=filter_name, error=error,
                         dispatcher=dispatcher)


def emit_filter_triggered(
    req_id: str, action: str, filter_name: str, message: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.filter_triggered",
                         action=action, filter_name=filter_name,
                         message=message or None,
                         dispatcher=dispatcher)


def emit_process_response_error(
    req_id: str, filter_name: str, error: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.process_response_error",
                         level="ERROR",
                         filter_name=filter_name, error=error,
                         dispatcher=dispatcher)


def emit_stream_first_chunk(
    req_id: str, size: int, preview: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_first_chunk",
                         size=size, preview=preview or None,
                         dispatcher=dispatcher)


def emit_stream_chunk_progress(
    req_id: str, count: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_chunk_progress",
                         level="DEBUG",
                         count=count, dispatcher=dispatcher)


def emit_stream_filter_error(
    req_id: str, filter_name: str, error: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_filter_error",
                         level="ERROR",
                         filter_name=filter_name, error=error,
                         dispatcher=dispatcher)


def emit_stream_retry(
    req_id: str, filter_name: str, chunks_processed: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_retry",
                         filter_name=filter_name,
                         chunks_processed=chunks_processed,
                         dispatcher=dispatcher)


def emit_stream_stop(
    req_id: str, filter_name: str, chunks_processed: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_stop",
                         filter_name=filter_name,
                         chunks_processed=chunks_processed,
                         dispatcher=dispatcher)


def emit_stream_exhausted(
    req_id: str, total_chunks: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_exhausted",
                         level="DEBUG",
                         total_chunks=total_chunks,
                         dispatcher=dispatcher)


def emit_buffer_orphaned(
    req_id: str, filter_name: str, chunks: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.buffer_orphaned",
                         level="DEBUG",
                         filter_name=filter_name, chunks=chunks,
                         dispatcher=dispatcher)


def emit_stream_started(
    req_id: str, has_upstream: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_started",
                         has_upstream=has_upstream,
                         dispatcher=dispatcher)


def emit_stream_request_filters_done(
    req_id: str, has_upstream: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_request_filters_done",
                         has_upstream=has_upstream,
                         dispatcher=dispatcher)


def emit_stream_finalizers_built(
    req_id: str, finalizer_count: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_finalizers_built",
                         finalizer_count=finalizer_count,
                         dispatcher=dispatcher)


def emit_stream_completed(
    req_id: str, execution_usage: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.stream_completed",
                         execution_usage=execution_usage,
                         dispatcher=dispatcher)


def emit_run_stream_entry(
    req_id: str, has_upstream: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.run_stream_entry",
                         has_upstream=has_upstream,
                         dispatcher=dispatcher)


def emit_phase1_done(
    req_id: str, has_upstream: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase1_done",
                         has_upstream=has_upstream,
                         dispatcher=dispatcher)


def emit_phase2_start(
    req_id: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase2_start",
                         dispatcher=dispatcher)


def emit_phase2_end(
    req_id: str, captured_chunks: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase2_end",
                         captured_chunks=captured_chunks,
                         dispatcher=dispatcher)


def emit_no_upstream(
    req_id: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.no_upstream",
                         level="WARN",
                         dispatcher=dispatcher)


def emit_phase3_retry_start(
    req_id: str, max_retries: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase3_retry_start",
                         max_retries=max_retries,
                         dispatcher=dispatcher)


def emit_phase3_retry_result(
    req_id: str, retry_count: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase3_retry_result",
                         level="DEBUG",
                         retry_count=retry_count,
                         dispatcher=dispatcher)


def emit_phase3_retry_end(
    req_id: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase3_retry_end",
                         level="DEBUG",
                         dispatcher=dispatcher)


def emit_phase4_yield_content(
    req_id: str, chunk_size: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase4_yield_content",
                         level="DEBUG",
                         chunk_size=chunk_size,
                         dispatcher=dispatcher)


def emit_phase4_skip_tc(
    req_id: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase4_skip_tc",
                         level="DEBUG",
                         dispatcher=dispatcher)


def emit_phase4_yield_tc(
    req_id: str, tool_call_index: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase4_yield_tc",
                         level="DEBUG",
                         tool_call_index=tool_call_index,
                         dispatcher=dispatcher)


def emit_phase4_error(
    req_id: str, error: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase4_error",
                         level="ERROR",
                         error=error,
                         dispatcher=dispatcher)


def emit_phase4_fr_stop(
    req_id: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_pipeline_event(req_id, "execution.pipeline.phase4_fr_stop",
                         dispatcher=dispatcher)
