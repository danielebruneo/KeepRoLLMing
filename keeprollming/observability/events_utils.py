"""Utils event emission helpers for O7 migration.

Provides ``emit_utils_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="utils", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call falls back to ``log()``
for the configurable PLAIN and JSON projectors.

Event type mapping (legacy log() → RuntimeEvent.type):

    dump_failed_payload_error               → execution.utils.dump_failed

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_utils_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a utils RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.utils.dump_failed").
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
    source = EventSource(domain="execution", component="utils")
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


def emit_dump_failed(
    req_id: str, error: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_utils_event(req_id, "execution.utils.dump_failed",
                         level="WARN",
                         error=error or None,
                         dispatcher=dispatcher)
