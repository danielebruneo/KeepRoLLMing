"""Execution event emission helpers for O7 migration.

Provides ``emit_execution_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="chat", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy log() → RuntimeEvent.type):

    http_in                         → execution.chat.http_in
    request_received                → execution.chat.request_received
    tool_call                       → execution.chat.tool_call
    tool_result                     → execution.chat.tool_result
    conv_{role}                     → execution.chat.conversation
    route_not_found                 → execution.chat.route_not_found
    missing_upstream_url            → execution.chat.missing_upstream
    invalid_upstream_url            → execution.chat.invalid_upstream
    route_resolved                  → execution.chat.route_resolved
    override_applied                → execution.chat.override
    upstream_req_repacked           → execution.chat.repacked
    fallback_chain_available        → execution.chat.fallback_chain
    chat_request_start              → execution.chat.request_start
    chat_request_route              → execution.chat.request_route
    upstream_http_error             → execution.chat.upstream_error
    fallback_to_next_model          → execution.chat.fallback
    pipeline_process_response_error → execution.chat.pipeline_error
    assistant                       → execution.chat.assistant
    http_out                        → execution.chat.http_out
    upstream_request_timeout        → execution.chat.timeout
    upstream_request_failed         → execution.chat.failed
    strip_image_retry               → execution.chat.strip_image_retry
    strip_image_no_more_images      → execution.chat.strip_image_done
    strip_image_retry_exception     → execution.chat.strip_image_error
    strip_image_retry_failed        → execution.chat.strip_image_failed

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .events import EventSource, RuntimeEvent


def emit_execution_event(
    req_id: str,
    event_type: str,
    level: str = "BASIC",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit an execution RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.chat.http_in").
    level : str
        Log level: DEBUG, INFO, BASIC, WARN, ERROR. Default "BASIC".
    dispatcher : EventDispatcher | None
        Optional dispatcher. When None the call is a no-op.
    **data : Any
        Event payload fields.

    Returns
    -------
    RuntimeEvent | None
        The event that was created, or None if no dispatcher available.
    """
    source = EventSource(domain="execution", component="chat")
    event = RuntimeEvent(
        type=event_type,
        timestamp_ns=time.time_ns(),
        source=source,
        data=data,
        req_id=req_id,
        level=level,
    )
    if dispatcher is None:
        # Fallback: fetch global dispatcher so callers that omit the
        # parameter still emit events through the configured pipeline.
        from ..app import get_event_dispatcher
        dispatcher = get_event_dispatcher()
    if dispatcher is not None:
        emit_fn = getattr(dispatcher, "emit", None)
        if emit_fn is not None:
            emit_fn(event)
    return event


# ── Convenience wrappers (mirror legacy log() call signatures) ──────────


def emit_http_in(
    req_id: str, client_model: str, stream: bool = False,
    max_tokens: Optional[int] = None, message_count: int = 0,
    user_id: str = "", conv_id: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.http_in",
                         client_model=client_model, stream=stream,
                         max_tokens=max_tokens, message_count=message_count,
                         user_id=user_id or None, conv_id=conv_id or None,
                         dispatcher=dispatcher)


def emit_request_received(
    req_id: str, header: Any, body_json: Any,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.request_received",
                         level="TRACE",
                         header=header, body_json=body_json,
                         dispatcher=dispatcher)


def emit_tool_call(
    req_id: str, tool_calls: Any,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.tool_call",
                         tool_calls=tool_calls, dispatcher=dispatcher)


def emit_tool_result(
    req_id: str, tool_call_id: Optional[str], name: Optional[str],
    content: Any,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.tool_result",
                         tool_call_id=tool_call_id, name=name,
                         content=content, dispatcher=dispatcher)


def emit_conversation(
    req_id: str, role: str, text: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.conversation",
                         role=role, text=text, dispatcher=dispatcher)


def emit_route_not_found(
    req_id: str, client_model: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.route_not_found",
                         level="ERROR",
                         client_model=client_model, dispatcher=dispatcher)


def emit_missing_upstream(
    req_id: str, route: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.missing_upstream",
                         level="ERROR",
                         route=route, dispatcher=dispatcher)


def emit_invalid_upstream(
    req_id: str, route: str, upstream_url: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.invalid_upstream",
                         level="ERROR",
                         route=route, upstream_url=upstream_url,
                         dispatcher=dispatcher)


def emit_route_resolved(
    req_id: str, client_model: str, resolved_route: str,
    model: str, upstream_model: str, summary_model: str,
    passthrough_enabled: bool, ctx_len: int,
    max_tokens_default: int, parent_routes: list,
    dispatcher: Optional[Any] = None,
    **extra_data: Any,
) -> None:
    data: dict[str, Any] = dict(
        client_model=client_model,
        resolved_route=resolved_route, model=model,
        upstream_model=upstream_model,
        summary_model=summary_model,
        passthrough_enabled=passthrough_enabled,
        ctx_len=ctx_len,
        max_tokens_default=max_tokens_default,
        parent_routes=parent_routes,
    )
    data.update(extra_data)
    return emit_execution_event(req_id, "execution.chat.route_resolved",
                         dispatcher=dispatcher, **data)


def emit_override(
    req_id: str, param: str, old_value: Any, new_value: Any,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.override",
                         param=param, old_value=old_value,
                         new_value=new_value, dispatcher=dispatcher)


def emit_repacked(
    req_id: str, did_summarize: bool, passthrough: bool,
    upstream_url: str, prompt_tokens: int,
    adjusted_max_tokens: Optional[int],
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.repacked",
                         did_summarize=did_summarize,
                         passthrough=passthrough,
                         upstream_url=upstream_url,
                         prompt_tokens=prompt_tokens,
                         adjusted_max_tokens=adjusted_max_tokens,
                         dispatcher=dispatcher)


def emit_fallback_chain(
    req_id: str, chain: Any, primary_model: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.fallback_chain",
                         chain=chain, primary_model=primary_model,
                         dispatcher=dispatcher)


def emit_request_start(
    req_id: str, stream: bool = False,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.request_start",
                         stream=stream, dispatcher=dispatcher)


def emit_request_route(
    req_id: str, stream: bool, route: str,
    filters: Optional[list[str]] = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.request_route",
                         stream=stream, route=route,
                         filters=filters or [], dispatcher=dispatcher)


def emit_upstream_error(
    req_id: str, status: int, url: str, route: str,
    upstream_model: str, body: str,
    request_payload: Optional[Any] = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.upstream_error",
                         level="ERROR",
                         status=status, url=url, route=route,
                         upstream_model=upstream_model,
                         body=body, request_payload=request_payload,
                         dispatcher=dispatcher)


def emit_fallback(
    req_id: str, from_model: str, to_model: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.fallback",
                         from_model=from_model, to_model=to_model,
                         dispatcher=dispatcher)


def emit_pipeline_error(
    req_id: str, error: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.pipeline_error",
                         level="ERROR",
                         error=error, dispatcher=dispatcher)


def emit_assistant(
    req_id: str, content: str, total_length: int,
    tool_calls: Optional[list] = None,
    reasoning_content: str = "", reasoning_length: int = 0,
    finish_reason: Optional[str] = None,
    dispatcher: Optional[Any] = None,
) -> None:
    data: dict[str, Any] = dict(
        content=content if content else "",
        total_length=total_length,
    )
    if tool_calls:
        data["tool_calls"] = tool_calls
    if reasoning_content:
        data["reasoning_content"] = reasoning_content
        data["reasoning_length"] = reasoning_length
    if finish_reason:
        data["finish_reason"] = finish_reason
    return emit_execution_event(req_id, "execution.chat.assistant",
                         dispatcher=dispatcher, **data)


def emit_http_out(
    req_id: str, status: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.http_out",
                         status=status, dispatcher=dispatcher)


def emit_timeout(
    req_id: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.timeout",
                         level="ERROR", dispatcher=dispatcher)


def emit_failed(
    req_id: str, error: str, url: str, route: str,
    upstream_model: str, tb: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.failed",
                         level="ERROR",
                         error=error, url=url, route=route,
                         upstream_model=upstream_model,
                         tb=tb, dispatcher=dispatcher)


def emit_strip_image_retry(
    req_id: str, attempt: int, remaining_images: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.strip_image_retry",
                         attempt=attempt, remaining_images=remaining_images,
                         dispatcher=dispatcher)


def emit_strip_image_done(
    req_id: str, attempt: int,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.strip_image_done",
                         level="WARN",
                         attempt=attempt, dispatcher=dispatcher)


def emit_strip_image_error(
    req_id: str, attempt: int, error: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.strip_image_error",
                         level="WARN",
                         attempt=attempt, error=error,
                         dispatcher=dispatcher)


def emit_strip_image_failed(
    req_id: str, attempt: int, status: int, body: str,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.strip_image_failed",
                         level="WARN",
                         attempt=attempt, status=status, body=body,
                         dispatcher=dispatcher)


def emit_cache_metrics(
    req_id: str, cached_tokens: int, prompt_tokens: int, cache_pct: float,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.cache_metrics",
                         cached_tokens=cached_tokens,
                         prompt_tokens=prompt_tokens,
                         cache_pct=cache_pct,
                         dispatcher=dispatcher)


def emit_performance_metrics(
    req_id: str,
    metrics: dict[str, Any],
    model: str,
    route_name: str,
    completion_tokens_source: str,
    dispatcher: Optional[Any] = None,
) -> None:
    """Emit the per-request derived metrics stored by the performance consumer.

    ``metrics`` must be the output of
    :func:`keeprollming.performance.compute_request_performance`, so this
    operational event remains semantically identical to the JSONL/dashboard
    performance record.
    """
    return emit_execution_event(
        req_id,
        "execution.chat.performance_metrics",
        model=model,
        route_name=route_name,
        completion_tokens_source=completion_tokens_source,
        **metrics,
        dispatcher=dispatcher,
    )


def emit_derived_performance_metrics(
    req_id: str,
    *,
    elapsed_ms: Any,
    completion_tokens: Any,
    ttft_ms: Any,
    prompt_tokens: Any,
    total_tokens: Any,
    cached_prompt_tokens: Any = None,
    model: str,
    route_name: str,
    completion_tokens_source: str,
    dispatcher: Optional[Any] = None,
) -> None:
    """Calculate and emit the same per-request values used by the dashboard."""
    from ..performance import compute_request_performance

    metrics = compute_request_performance(
        elapsed_ms=elapsed_ms,
        completion_tokens=completion_tokens,
        ttft_ms=ttft_ms,
        prompt_tokens=prompt_tokens,
        total_tokens=total_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
    )
    return emit_performance_metrics(
        req_id,
        metrics,
        model=model,
        route_name=route_name,
        completion_tokens_source=completion_tokens_source,
        dispatcher=dispatcher,
    )


def emit_summary_bypassed(
    req_id: str, reason: str, prompt_tok_est: int = 0, threshold: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.summary_bypassed",
                         reason=reason,
                         prompt_tok_est=prompt_tok_est or None,
                         threshold=threshold or None,
                         dispatcher=dispatcher)


def emit_max_tokens_clamped(
    req_id: str, requested: int, adjusted: int, ctx_len: int,
    prompt_tokens: Optional[int] = None, safety_margin: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_execution_event(req_id, "execution.chat.max_tokens_clamped",
                         level="WARN",
                         requested=requested, adjusted=adjusted,
                         ctx_len=ctx_len, prompt_tokens=prompt_tokens,
                         safety_margin=safety_margin,
                         dispatcher=dispatcher)


# ── Performance events (O10) ──────────────────────────────────────

def emit_performance_request_complete(
    req_id: str,
    model: str,
    route_name: str,
    route_hierarchy: list[str],
    stream: bool,
    elapsed_ms: float,
    ttft_ms: Optional[float],
    completion_tokens: Optional[int],
    prompt_tokens: Optional[int],
    total_tokens: Optional[int],
    finish_reason: Optional[str],
    did_summarize: bool,
    passthrough: bool,
    completion_tokens_source: str,
    upstream_attempts: int = 0,
    usage_reported_attempts: int = 0,
    recovery_count: int = 0,
    retry_amplification_ratio: Optional[float] = None,
    usage_complete: bool = False,
    upstream_prompt_tokens: Optional[int] = None,
    upstream_completion_tokens: Optional[int] = None,
    upstream_total_tokens: Optional[int] = None,
    cached_prompt_tokens: Optional[int] = None,
    dispatcher: Optional[Any] = None,
) -> None:
    """Emit execution.performance.request_complete event (O10).

    Carries all fields required by PerformanceConsumer to replicate
    record_request_performance() behavior with full parity.
    """
    source = EventSource(domain="execution", component="performance")
    event = RuntimeEvent(
        type="execution.performance.request_complete",
        timestamp_ns=time.time_ns(),
        source=source,
        data={
            "model": model,
            "route_name": route_name,
            "route_hierarchy": route_hierarchy,
            "req_id": req_id,
            "stream": stream,
            "elapsed_ms": elapsed_ms,
            "ttft_ms": ttft_ms,
            "completion_tokens": completion_tokens,
            "prompt_tokens": prompt_tokens,
            "total_tokens": total_tokens,
            "finish_reason": finish_reason,
            "did_summarize": did_summarize,
            "passthrough": passthrough,
            "completion_tokens_source": completion_tokens_source,
            "upstream_attempts": upstream_attempts,
            "usage_reported_attempts": usage_reported_attempts,
            "recovery_count": recovery_count,
            "retry_amplification_ratio": retry_amplification_ratio,
            "usage_complete": usage_complete,
            "upstream_prompt_tokens": upstream_prompt_tokens,
            "upstream_completion_tokens": upstream_completion_tokens,
            "upstream_total_tokens": upstream_total_tokens,
            "cached_prompt_tokens": cached_prompt_tokens,
        },
        req_id=req_id,
        # This is the terminal operational summary, so BASIC PLAIN must
        # receive it without admitting generic INFO pipeline events.
        level="BASIC",
    )
    if dispatcher is not None:
        emit_fn = getattr(dispatcher, "emit", None)
        if emit_fn is not None:
            emit_fn(event)


# ── Request capture events (O12) ──────────────────────────────────

def emit_request_capture(
    req_id: str,
    raw_body: Dict[str, Any],
    client_model: str,
    resolved_route: str,
    upstream_model: str,
    upstream_url: str,
    route_hierarchy: Optional[list[str]] = None,
    dispatcher: Optional[Any] = None,
    **extra_metadata: Any,
) -> None:
    """Emit request.capture.raw_inbound event for Raw Request Capture (O12).

    Carries the effective upstream payload (post-route-resolution,
    pre-filter-chain) plus correlation metadata. Consumed by
    RequestCaptureConsumer for persistence.

    Parameters
    ----------
    req_id : str
        Request correlator.
    raw_body : dict
        The effective upstream payload to capture.
    client_model : str
        Model requested by the client.
    resolved_route : str
        Route that handled the request.
    upstream_model : str
        Actual upstream model used.
    upstream_url : str
        Target upstream endpoint.
    route_hierarchy : list[str] | None
        Extends chain for the resolved route.
    dispatcher : EventDispatcher | None
        Optional dispatcher. When None the call is a no-op.
    **extra_metadata : Any
        Additional metadata fields to include.
    """
    source = EventSource(domain="request", component="capture")
    data: dict[str, Any] = {
        "raw_body": raw_body,
        "client_model": client_model,
        "resolved_route": resolved_route,
        "upstream_model": upstream_model,
        "upstream_url": upstream_url,
    }
    if route_hierarchy is not None:
        data["route_hierarchy"] = route_hierarchy
    data.update(extra_metadata)

    event = RuntimeEvent(
        type="request.capture.raw_inbound",
        timestamp_ns=time.time_ns(),
        source=source,
        data=data,
        req_id=req_id,
        level="DEBUG",
    )
    if dispatcher is not None:
        emit_fn = getattr(dispatcher, "emit", None)
        if emit_fn is not None:
            emit_fn(event)
