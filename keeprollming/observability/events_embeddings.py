"""Embeddings event emission helpers for O7 migration.

Provides ``emit_embedding_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="embeddings", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call falls back to ``log()``
for the configurable PLAIN and JSON projectors.

Event type mapping (legacy log() → RuntimeEvent.type):

    embedding_request                       → execution.embeddings.request
    embedding_request_debug                 → execution.embeddings.request_debug
    embedding_request_failed                → execution.embeddings.failed
    embedding_request_timeout               → execution.embeddings.timeout

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_embedding_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit an embeddings RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.embeddings.request").
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
    source = EventSource(domain="execution", component="embeddings")
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


def emit_request(
    req_id: str, model: str = "", input_length: int = 0,
    upstream_url: str = "", route: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_embedding_event(req_id, "execution.embeddings.request",
                         model=model or None,
                         input_length=input_length or None,
                         upstream_url=upstream_url or None,
                         route=route or None,
                         dispatcher=dispatcher)


def emit_request_debug(
    req_id: str, body_json: Any = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_embedding_event(req_id, "execution.embeddings.request_debug",
                         body_json=body_json,
                         dispatcher=dispatcher)


def emit_failed(
    req_id: str, error: str = "", traceback: str = "",
    upstream_url: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_embedding_event(req_id, "execution.embeddings.failed",
                         level="ERROR",
                         error=error or None,
                         traceback=traceback or None,
                         upstream_url=upstream_url or None,
                         dispatcher=dispatcher)


def emit_timeout(
    req_id: str, upstream_url: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_embedding_event(req_id, "execution.embeddings.timeout",
                         level="ERROR",
                         upstream_url=upstream_url or None,
                         dispatcher=dispatcher)
