"""App event emission helpers for O7 migration.

Provides ``emit_app_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="app", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op.

Event type mapping (legacy log() → RuntimeEvent.type):

    perf_logs_dir                           → execution.app.perf_logs_dir
    config_reloaded                         → execution.app.config_reloaded
    config_reload_failed                    → execution.app.config_reload_failed
    app_starting                            → execution.app.starting
    app_stopping                            → execution.app.stopping
    not_found                               → execution.app.not_found

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_app_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit an app RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator (may be empty for startup events).
    event_type : str
        Full hierarchical event type (e.g. "execution.app.starting").
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
    source = EventSource(domain="execution", component="app")
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


def emit_perf_logs_dir(
    message: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_app_event("", "execution.app.perf_logs_dir",
                         message=message or None,
                         dispatcher=dispatcher)


def emit_config_reloaded(
    message: str = "",
    config_mtime: float | None = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_app_event("", "execution.app.config_reloaded",
                         message=message or None, config_mtime=config_mtime,
                         dispatcher=dispatcher)


def emit_config_reload_failed(
    error: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_app_event(
        "", "execution.app.config_reload_failed", level="ERROR",
        error=error, dispatcher=dispatcher,
    )


def emit_starting(
    message: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_app_event("", "execution.app.starting",
                         message=message or None,
                         dispatcher=dispatcher)


def emit_stopping(
    message: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_app_event("", "execution.app.stopping",
                         message=message or None,
                         dispatcher=dispatcher)


def emit_not_found(
    path: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_app_event("", "execution.app.not_found",
                         level="WARN",
                         path=path or None,
                         dispatcher=dispatcher)
