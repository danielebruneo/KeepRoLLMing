"""Downstream event emission helpers for O8 migration.

Provides ``emit_downstream_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="downstream", component="delivery", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    chunk_sent                      → downstream.delivery.chunk.sent
    delivery_completed              → downstream.delivery.completed
    delivery_closed                 → downstream.delivery.closed
    delivery_failed                 → downstream.delivery.failed

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_downstream_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a downstream RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "downstream.delivery.chunk.sent").
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
    source = EventSource(domain="downstream", component="delivery")
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
    return event


# ── Convenience wrappers (mirror legacy log() call signatures) ──────────


def emit_chunk_sent(
    req_id: str, chunk_index: int = 0, delta_len: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_downstream_event(req_id, "downstream.delivery.chunk.sent",
                         chunk_index=chunk_index,
                         delta_len=delta_len or None,
                         dispatcher=dispatcher)


def emit_delivery_completed(
    req_id: str, total_chunks: int = 0, elapsed_ms: float = 0.0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_downstream_event(req_id, "downstream.delivery.completed",
                         total_chunks=total_chunks or None,
                         elapsed_ms=elapsed_ms or None,
                         dispatcher=dispatcher)


def emit_delivery_closed(
    req_id: str, finish_reason: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_downstream_event(req_id, "downstream.delivery.closed",
                         finish_reason=finish_reason or None,
                         dispatcher=dispatcher)


def emit_delivery_failed(
    req_id: str, error: str, status: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_downstream_event(req_id, "downstream.delivery.failed",
                         level="ERROR",
                         error=error, status=status or None,
                         dispatcher=dispatcher)
