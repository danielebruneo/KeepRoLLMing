"""Bounded, request-scoped recovery shared by the two loop stoppers."""

import asyncio
import copy
import math
from typing import Any

from keeprollming.orchestrator.filter import StreamingResponse
from keeprollming.orchestrator.filters.events import emit_filter_event


def retry_limit(settings: dict) -> int:
    """max_attempts is an alias for recovery budget, never a detection threshold."""
    return settings.get("max_retries", settings.get("max_attempts", 2))


def validate_settings(settings) -> None:
    for name in ("max_retries", "max_attempts", "max_repeats", "fuzzy_max_repeats", "fuzzy_look_back"):
        if name in settings and (type(settings[name]) is not int or settings[name] < 0):
            raise ValueError(f"{name} must be a non-negative integer")
    timeout = settings.get("retry_timeout")
    if timeout is not None and (isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("retry_timeout must be a finite positive number")
    threshold = settings.get("fuzzy_threshold")
    if threshold is not None and (isinstance(threshold, bool) or not 0 <= threshold <= 1):
        raise ValueError("fuzzy_threshold must be between 0 and 1")


def response_from_json(body: Any) -> StreamingResponse | None:
    if not isinstance(body, dict) or body.get("error"):
        return None
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return None
    content = message.get("content") or ""
    calls = message.get("tool_calls") or []
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    if not (content or calls or reasoning):
        return None
    reason = choices[0].get("finish_reason")
    return StreamingResponse(
        content=content,
        tool_calls=calls,
        reasoning_content=reasoning,
        finish_reason=reason if reason not in (None, "stop") else ("tool_calls" if calls else "stop"),
        model=body.get("model", ""),
        usage=body.get("usage"),
    )


def emit_recovery(context, name: str, outcome: str, **data) -> None:
    emit_filter_event(context, name, "filter.loop.recovery", level="BASIC", filter=name, outcome=outcome, **data)


async def recover(filter_, response, context, detect, augment, fallback):
    """Recheck every retry, preserving request settings and protocol state.

    Production always uses the endpoint-owned caller, transport and timeout.
    Standalone filters can still use the base HTTP helper with the full payload.
    Counters live on the request context, so re-entering the pipeline cannot
    reset an exhausted filter's budget.
    """
    name = filter_.config.name
    budgets = context.metadata.setdefault("loop_retries", {})
    while response.finish_reason not in ("length", "content_filter"):
        history = context.upstream_payload.get("messages", [])
        # Direct filter callers may supply explicit conversation metadata.
        history = context.metadata.get("conversation_history", history)
        detected = detect(response, history)
        if not detected:
            return response
        used = budgets.get(name, 0)
        if used >= filter_.config.max_retries or context.metadata.get("recovery_count", 0) >= 10:
            emit_recovery(context, name, "exhausted", attempts=used)
            return fallback(response, detected)
        messages = augment(copy.deepcopy(history), response, detected)
        payload = copy.deepcopy(context.upstream_payload)
        payload.update(messages=messages, stream=False)
        budgets[name] = used + 1
        emit_recovery(context, name, "retry", attempt=used + 1, max_retries=filter_.config.max_retries)
        try:
            if context._upstream_caller:
                if filter_.config.retry_timeout is None:
                    operation = context._upstream_caller(payload)
                else:
                    operation = context._upstream_caller(payload, request_timeout_override=filter_.config.retry_timeout)
            else:
                context.metadata["recovery_count"] = context.metadata.get("recovery_count", 0) + 1
                operation = filter_._make_http_retry(
                    messages,
                    filter_._resolve_upstream_model(context),
                    filter_._resolve_upstream_url(context),
                    original_payload=payload,
                    api_key=getattr(filter_, "_api_key", None),
                    timeout=filter_.config.retry_timeout or context.metadata.get("request_timeout", 120),
                )
            if filter_.config.retry_timeout is None:
                body = await operation
            else:
                body = await asyncio.wait_for(operation, timeout=filter_.config.retry_timeout)
        except asyncio.CancelledError:
            emit_recovery(context, name, "cancelled", attempt=used + 1)
            raise
        except Exception as exc:
            emit_recovery(
                context,
                name,
                "failed",
                error_type=type(exc).__name__,
                status=getattr(getattr(exc, "response", None), "status_code", None),
            )
            return fallback(response, detected)
        candidate = response_from_json(body)
        if candidate is None:
            emit_recovery(context, name, "failed", reason="empty_or_invalid_response")
            return fallback(response, detected)
        if not candidate.model:
            candidate.model = response.model
        context.upstream_payload = payload
        if "conversation_history" in context.metadata:
            context.metadata["conversation_history"] = messages
        context.metadata["response_replaced"] = True
        response = candidate
        emit_recovery(context, name, "received", finish_reason=response.finish_reason, attempt=used + 1)
        if context.metadata.get("pipeline_response_reentry"):
            # Let rewrite and both loop controls inspect this candidate before
            # deciding on the next recovery; direct filter use loops locally.
            return response
    return response
