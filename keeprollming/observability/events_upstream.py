"""Upstream event emission helpers for O7 migration.

Provides ``emit_upstream_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="upstream", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op.

Event type mapping (legacy log() → RuntimeEvent.type):

    response_received                     → execution.upstream.response_received
    ctx_len                               → execution.upstream.ctx_len
    ctx_len_fallback                      → execution.upstream.ctx_len_fallback
    all_endpoints_failed                  → execution.upstream.all_endpoints_failed
    override_applied                      → execution.upstream.override_applied

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_upstream_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit an upstream RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.upstream.response_received").
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
    source = EventSource(domain="execution", component="upstream")
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


def emit_response_received(
    url: str, method: str, status: int, elapsed_ms: float = 0.0,
    headers: dict = None, body: str = "", note: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_upstream_event("", "execution.upstream.response_received",
                         url=url, method=method, status=status,
                         elapsed_ms=elapsed_ms or None,
                         headers=headers or {},
                         body=body or None,
                         note=note or None,
                         dispatcher=dispatcher)


def emit_ctx_len(
    upstream_model: str, ctx_len: int, source: str = "default",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_upstream_event("", "execution.upstream.ctx_len",
                         upstream_model=upstream_model,
                         ctx_len=ctx_len, source=source,
                         dispatcher=dispatcher)


def emit_ctx_len_fallback(
    upstream_model: str, ctx_len: int = 0, err: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_upstream_event("", "execution.upstream.ctx_len_fallback",
                         level="WARN",
                         upstream_model=upstream_model,
                         ctx_len=ctx_len or None,
                         err=err or None,
                         dispatcher=dispatcher)


def emit_all_endpoints_failed(
    upstream_model: str, ctx_len: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_upstream_event("", "execution.upstream.all_endpoints_failed",
                         level="WARN",
                         upstream_model=upstream_model,
                         ctx_len=ctx_len or None,
                         dispatcher=dispatcher)


def emit_override_applied(
    req_id: str, param: str, old_value: Any, new_value: Any,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_upstream_event(req_id, "execution.upstream.override_applied",
                         param=param, old_value=old_value,
                         new_value=new_value,
                         dispatcher=dispatcher)


def emit_connection_error(
    req_id: str, error_type: str, upstream_url: str,
    model: str = "", elapsed_ms: float = 0.0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_upstream_event(req_id, "execution.upstream.connection_error",
                         level="ERROR",
                         error_type=error_type,
                         upstream_url=upstream_url,
                         model=model or None,
                         elapsed_ms=elapsed_ms or None,
                         dispatcher=dispatcher)
