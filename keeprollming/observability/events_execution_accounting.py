"""Execution accounting event emission helpers for O8 migration.

Provides ``emit_execution_accounting_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="accounting", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    usage_captured                  → execution.accounting.usage.captured
    attempt_recorded                → execution.accounting.usage.attempt_recorded
    usage_finalized                 → execution.accounting.usage.finalized

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_execution_accounting_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit an execution accounting RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.accounting.usage.captured").
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
    source = EventSource(domain="execution", component="accounting")
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


def emit_usage_captured(
    req_id: str, prompt_tokens: int = 0, completion_tokens: int = 0,
    total_tokens: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_accounting_event(req_id, "execution.accounting.usage.captured",
                         prompt_tokens=prompt_tokens or None,
                         completion_tokens=completion_tokens or None,
                         total_tokens=total_tokens or None,
                         dispatcher=dispatcher)


def emit_attempt_recorded(
    req_id: str, model: str = "", attempt: int = 0,
    tokens: int = 0, cost: float = 0.0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_accounting_event(req_id, "execution.accounting.usage.attempt_recorded",
                         model=model or None,
                         attempt=attempt or None,
                         tokens=tokens or None,
                         cost=cost or None,
                         dispatcher=dispatcher)


def emit_usage_finalized(
    req_id: str, total_attempts: int = 0,
    total_prompt_tokens: int = 0, total_completion_tokens: int = 0,
    total_cost: float = 0.0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_accounting_event(req_id, "execution.accounting.usage.finalized",
                         total_attempts=total_attempts or None,
                         total_prompt_tokens=total_prompt_tokens or None,
                         total_completion_tokens=total_completion_tokens or None,
                         total_cost=total_cost or None,
                         dispatcher=dispatcher)
