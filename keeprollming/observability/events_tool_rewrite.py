"""Tool-rewrite event emission helpers for O8 migration.

Provides ``emit_tool_rewrite_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="tool_rewrite", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    parse_error                     → execution.tool_rewrite.parse_error
    streaming_error                 → execution.tool_rewrite.streaming_error
    body_error                      → execution.tool_rewrite.body_error

All events use level="ERROR" by default.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_tool_rewrite_event(
    req_id: str,
    event_type: str,
    level: str = "ERROR",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a tool-rewrite RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.tool_rewrite.parse_error").
    level : str
        Log level: DEBUG, INFO, WARN, ERROR. Default "ERROR".
    dispatcher : EventDispatcher | None
        Optional dispatcher. When None the call is a no-op.
    **data : Any
        Event payload fields.

    Returns
    -------
    RuntimeEvent | None
        The event that was created, or None if no dispatcher available.
    """
    source = EventSource(domain="execution", component="tool_rewrite")
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


def emit_parse_error(
    req_id: str, error: str = "", traceback: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_tool_rewrite_event(req_id, "execution.tool_rewrite.parse_error",
                         error=error or None,
                         traceback=traceback or None,
                         dispatcher=dispatcher)


def emit_streaming_error(
    req_id: str, error: str = "", traceback: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_tool_rewrite_event(req_id, "execution.tool_rewrite.streaming_error",
                         error=error or None,
                         traceback=traceback or None,
                         dispatcher=dispatcher)


def emit_body_error(
    req_id: str, error: str = "", traceback: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_tool_rewrite_event(req_id, "execution.tool_rewrite.body_error",
                         error=error or None,
                         traceback=traceback or None,
                         dispatcher=dispatcher)
