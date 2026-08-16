"""Routing event emission helpers for O8 migration.

Provides ``emit_routing_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="routing", component="resolution", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    routing_started                 → routing.resolution.started
    routing_resolved                → routing.resolution.resolved
    routing_failed                  → routing.resolution.failed

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_routing_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a routing RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "routing.resolution.started").
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
    source = EventSource(domain="routing", component="resolution")
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


def emit_started(
    req_id: str, client_model: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_routing_event(req_id, "routing.resolution.started",
                         client_model=client_model, dispatcher=dispatcher)


def emit_resolved(
    req_id: str, client_model: str, resolved_route: str,
    model: str, upstream_model: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_routing_event(req_id, "routing.resolution.resolved",
                         client_model=client_model,
                         resolved_route=resolved_route,
                         model=model, upstream_model=upstream_model,
                         dispatcher=dispatcher)


def emit_failed(
    req_id: str, client_model: str, error: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_routing_event(req_id, "routing.resolution.failed",
                         level="ERROR",
                         client_model=client_model,
                         error=error or None, dispatcher=dispatcher)


def emit_fallback_error(
    req_id: str, from_model: str, to_model: str,
    error_type: str = "", err_msg: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_routing_event(req_id, "routing.resolution.fallback_error",
                         level="WARN",
                         from_model=from_model,
                         to_model=to_model,
                         error_type=error_type or None,
                         err_msg=err_msg or None,
                         dispatcher=dispatcher)
