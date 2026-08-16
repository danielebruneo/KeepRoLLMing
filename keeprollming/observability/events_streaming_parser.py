"""Streaming parser event emission helpers for O8 migration.

Provides ``emit_streaming_parser_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="streaming.parser", component="parser", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    frame_received                  → streaming.parser.frame.received
    events_generated                → streaming.parser.events_generated
    usage_buffered                  → streaming.parser.usage_buffered
    flushed                         → streaming.parser.flushed
    invalid_frame                   → streaming.parser.invalid_frame

All events use level="DEBUG" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_streaming_parser_event(
    req_id: str,
    event_type: str,
    level: str = "DEBUG",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a streaming parser RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "streaming.parser.frame.received").
    level : str
        Log level: DEBUG, INFO, WARN, ERROR. Default "DEBUG".
    dispatcher : EventDispatcher | None
        Optional dispatcher. When None the call is a no-op.
    **data : Any
        Event payload fields.

    Returns
    -------
    RuntimeEvent | None
        The event that was created, or None if no dispatcher available.
    """
    source = EventSource(domain="streaming.parser", component="parser")
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


def emit_frame_received(
    req_id: str, frame_len: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_parser_event(req_id, "streaming.parser.frame.received",
                         frame_len=frame_len or None,
                         dispatcher=dispatcher)


def emit_events_generated(
    req_id: str, event_count: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_parser_event(req_id, "streaming.parser.events_generated",
                         event_count=event_count or None,
                         dispatcher=dispatcher)


def emit_usage_buffered(
    req_id: str, usage: Any = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_parser_event(req_id, "streaming.parser.usage_buffered",
                         usage=usage, dispatcher=dispatcher)


def emit_flushed(
    req_id: str, channel: str = "assistant", delta_len: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_parser_event(req_id, "streaming.parser.flushed",
                         channel=channel or None,
                         delta_len=delta_len or None,
                         dispatcher=dispatcher)


def emit_invalid_frame(
    req_id: str, reason: str = "", frame_snippet: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_streaming_parser_event(req_id, "streaming.parser.invalid_frame",
                         level="WARN",
                         reason=reason or None,
                         frame_snippet=frame_snippet or None,
                         dispatcher=dispatcher)
