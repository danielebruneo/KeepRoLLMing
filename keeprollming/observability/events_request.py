"""Request event emission helpers for O8 migration.

Provides ``emit_request_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="request", component="lifecycle", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    request_received                → request.lifecycle.received
    preprocessing_started           → request.lifecycle.preprocessing.started
    preprocessing_completed         → request.lifecycle.preprocessing.completed
    request_completed               → request.lifecycle.completed
    request_failed                  → request.lifecycle.failed
    request_cancelled               → request.lifecycle.cancelled

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_request_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a request RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "request.lifecycle.received").
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
    source = EventSource(domain="request", component="lifecycle")
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


def emit_received(
    req_id: str,
    client_model: str,
    stream: bool = False,
    endpoint: str = "/v1/chat/completions",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id,
        "request.lifecycle.received",
        client_model=client_model,
        stream=stream,
        endpoint=endpoint,
        dispatcher=dispatcher,
    )


def emit_preprocessing_started(
    req_id: str,
    endpoint: str = "/v1/chat/completions",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id, "request.lifecycle.preprocessing.started", endpoint=endpoint, dispatcher=dispatcher
    )


def emit_preprocessing_completed(
    req_id: str,
    stream: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(req_id, "request.lifecycle.preprocessing.completed", stream=stream, dispatcher=dispatcher)


def emit_completed(
    req_id: str,
    status: int = 200,
    elapsed_ms: float = 0.0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id,
        "request.lifecycle.completed",
        status=status,
        elapsed_ms=elapsed_ms,
        dispatcher=dispatcher,
    )


def emit_failed(
    req_id: str,
    error: str,
    status: int = 500,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id,
        "request.lifecycle.failed",
        level="ERROR",
        error=error,
        status=status,
        dispatcher=dispatcher,
    )


def emit_cancelled(
    req_id: str,
    reason: str = "",
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id,
        "request.lifecycle.cancelled",
        level=level,
        reason=reason or None,
        dispatcher=dispatcher,
    )


def emit_auth_rejected(
    req_id: str,
    *,
    route: str,
    endpoint: str,
    credential_present: bool,
    dispatcher: Optional[Any] = None,
) -> None:
    """Record an authentication rejection without ever retaining a secret."""
    return emit_request_event(
        req_id,
        "request.lifecycle.auth_rejected",
        level="WARN",
        route=route,
        endpoint=endpoint,
        credential_present=credential_present,
        dispatcher=dispatcher,
    )


def emit_admission_queued(
    req_id: str,
    *,
    route: str,
    max_concurrent: int,
    queue_timeout_ms: int,
    routing_priority: int = 0,
    service_route: str | None = None,
    queue_aging_interval_ms: int | None = None,
    queue_aging_priority_step: int | None = None,
    queue_aging_max_boost: int | None = None,
    deadline_policy_enabled: bool = False,
    first_token_target_remaining_ms: int | None = None,
    estimated_ttft_ms: float | None = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id,
        "request.lifecycle.admission.queued",
        route=route,
        max_concurrent=max_concurrent,
        queue_timeout_ms=queue_timeout_ms,
        routing_priority=routing_priority,
        service_route=service_route,
        queue_aging_interval_ms=queue_aging_interval_ms,
        queue_aging_priority_step=queue_aging_priority_step,
        queue_aging_max_boost=queue_aging_max_boost,
        deadline_policy_enabled=deadline_policy_enabled,
        first_token_target_remaining_ms=first_token_target_remaining_ms,
        estimated_ttft_ms=estimated_ttft_ms,
        dispatcher=dispatcher,
    )


def emit_admission_acquired(
    req_id: str,
    *,
    route: str,
    max_concurrent: int,
    waited: bool,
    routing_priority: int = 0,
    service_route: str | None = None,
    effective_routing_priority: int | None = None,
    queue_wait_ms: int | None = None,
    deadline_boost: int = 0,
    deadline_policy_enabled: bool = False,
    first_token_target_remaining_ms: int | None = None,
    estimated_ttft_ms: float | None = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id,
        "request.lifecycle.admission.acquired",
        route=route,
        max_concurrent=max_concurrent,
        waited=waited,
        routing_priority=routing_priority,
        service_route=service_route,
        effective_routing_priority=effective_routing_priority,
        queue_wait_ms=queue_wait_ms,
        deadline_boost=deadline_boost,
        deadline_policy_enabled=deadline_policy_enabled,
        first_token_target_remaining_ms=first_token_target_remaining_ms,
        estimated_ttft_ms=estimated_ttft_ms,
        dispatcher=dispatcher,
    )


def emit_admission_rejected(
    req_id: str,
    *,
    route: str,
    max_concurrent: int,
    queue_timeout_ms: int,
    routing_priority: int = 0,
    service_route: str | None = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_request_event(
        req_id,
        "request.lifecycle.admission.rejected",
        level="WARN",
        route=route,
        max_concurrent=max_concurrent,
        queue_timeout_ms=queue_timeout_ms,
        routing_priority=routing_priority,
        service_route=service_route,
        dispatcher=dispatcher,
    )


def emit_admission_released(route: str, *, dispatcher: Optional[Any] = None) -> None:
    return emit_request_event(
        "-",
        "request.lifecycle.admission.released",
        route=route,
        dispatcher=dispatcher,
    )
