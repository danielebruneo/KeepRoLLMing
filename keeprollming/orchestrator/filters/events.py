"""Filter event emission helpers for O6 migration.

Provides ``emit_filter_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="filter", component=<filter_name>,
and emits it through the ``event_dispatcher`` stored in
``FilterExecutionContext``.

When no dispatcher is available the call is a no-op (defensive).

Event type mapping (legacy FilterLogger method → RuntimeEvent.type):

    system_prompt_inserted              → filter.system_prompt.inserted
    system_prompt_overridden            → filter.system_prompt.overridden
    system_prompt_prepended             → filter.system_prompt.prepended
    nudge_triggered                     → filter.nudge.detected
    loop_detected                       → filter.tool_loop.detected
    tool_loop_detected                  → filter.tool_loop.detected
    tls_intervention                    → filter.tool_loop.intervention
    tls_retry                           → filter.tool_loop.retry
    tls_fallback                        → filter.tool_loop.fallback
    reasoning_loop_detected             → filter.reasoning_loop.detected
    rls_intervention                    → filter.reasoning_loop.intervention
    rls_fallback                        → filter.reasoning_loop.fallback
    tool_rewrite_applied                → filter.tool_rewrite.applied
    filter_chain_executed               → filter.chain.executed
    filter_error                        → filter.error
    filter_disabled                     → filter.disabled
    summary_stats                       → (diagnostic, not migrated)

All events use level="INFO" by default.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from keeprollming.observability import EventSource, RuntimeEvent


def emit_filter_event(
    context: Any,
    component: str,
    event_type: str,
    level: str = "INFO",
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a filter RuntimeEvent through the context's event_dispatcher.

    Parameters
    ----------
    context : FilterExecutionContext
        Must have ``req_id`` and ``event_dispatcher`` attributes.
    component : str
        Filter component name (e.g. "model_nudge", "tool_loop_stopper").
    event_type : str
        Full hierarchical event type (e.g. "filter.nudge.detected").
    level : str
        Log level: DEBUG, INFO, WARN, ERROR. Default "INFO".
    **data : Any
        Event payload fields.

    Returns
    -------
    RuntimeEvent | None
        The event that was emitted, or None if no dispatcher available.
    """
    source = EventSource(domain="filter", component=component)
    req_id = getattr(context, "req_id", None) or getattr(context, "metadata", {}).get("req_id")
    event = RuntimeEvent(
        type=event_type,
        timestamp_ns=time.time_ns(),
        source=source,
        data=data,
        req_id=req_id,
        level=level,
    )
    dispatcher = getattr(context, "event_dispatcher", None)
    if dispatcher is not None:
        emit_fn = getattr(dispatcher, "emit", None)
        if emit_fn is not None:
            emit_fn(event)
    return event


# ── Convenience wrappers (mirror FilterLogger method signatures) ─────

def emit_system_prompt_inserted(context: Any, prompt_preview: str) -> None:
    emit_filter_event(context, "system_prompt", "filter.system_prompt.inserted",
                      prompt_preview=prompt_preview[:80])


def emit_system_prompt_overridden(context: Any, prompt_preview: str, old_length: int) -> None:
    emit_filter_event(context, "system_prompt", "filter.system_prompt.overridden",
                      prompt_preview=prompt_preview[:80], old_length=old_length)


def emit_system_prompt_prepended(context: Any, prompt_preview: str, old_length: int) -> None:
    emit_filter_event(context, "system_prompt", "filter.system_prompt.prepended",
                      prompt_preview=prompt_preview[:80], old_length=old_length)


def emit_nudge_detected(context: Any, trigger_pattern: str, response_content: str,
                        nudge_attempt: int, action: str = "nudge", max_attempts: int = 3) -> None:
    emit_filter_event(context, "model_nudge", "filter.nudge.detected",
                      trigger_pattern=trigger_pattern,
                      response_content=response_content[:200],
                      nudge_attempt=nudge_attempt,
                      action=action,
                      max_attempts=max_attempts)


def emit_tool_loop_detected(context: Any, function_name: str, args_hash: str,
                            attempt: int = 1) -> None:
    emit_filter_event(context, "tool_loop_stopper", "filter.tool_loop.detected",
                      function_name=function_name,
                      args_hash=args_hash[:64],
                      attempt=attempt)


def emit_tls_intervention(context: Any, messages_count: int) -> None:
    emit_filter_event(context, "tool_loop_stopper", "filter.tool_loop.intervention",
                      messages_count=messages_count)


def emit_tls_retry(context: Any, model: str, messages_count: int) -> None:
    emit_filter_event(context, "tool_loop_stopper", "filter.tool_loop.retry",
                      model=model, messages_count=messages_count)


def emit_tls_fallback(context: Any, reason: str) -> None:
    emit_filter_event(context, "tool_loop_stopper", "filter.tool_loop.fallback",
                      reason=reason)


def emit_reasoning_loop_detected(context: Any, reasoning: str) -> None:
    emit_filter_event(context, "reasoning_loop_stopper", "filter.reasoning_loop.detected",
                      reasoning=reasoning[:200])


def emit_rls_intervention(context: Any, messages_count: int) -> None:
    emit_filter_event(context, "reasoning_loop_stopper", "filter.reasoning_loop.intervention",
                      messages_count=messages_count)


def emit_rls_fallback(context: Any) -> None:
    emit_filter_event(context, "reasoning_loop_stopper", "filter.reasoning_loop.fallback")


def emit_tool_rewrite_applied(context: Any, tool_name: str,
                              original_length: int, cleaned_length: int) -> None:
    emit_filter_event(context, "tool_rewrite", "filter.tool_rewrite.applied",
                      tool_name=tool_name,
                      original_length=original_length,
                      cleaned_length=cleaned_length)


def emit_filter_chain_executed(context: Any, filters_executed: list,
                               total_filters: int,
                               nudge_count: int = 0,
                               loop_count: int = 0) -> None:
    emit_filter_event(context, "filter_chain", "filter.chain.executed",
                      filters_executed=filters_executed,
                      total_filters=total_filters,
                      nudge_count=nudge_count,
                      loop_count=loop_count)


def emit_filter_error(context: Any, error_type: str, message: str) -> None:
    emit_filter_event(context, "filter_chain", "filter.error",
                      level="ERROR",
                      error_type=error_type,
                      message=message)


def emit_filter_disabled(context: Any) -> None:
    emit_filter_event(context, "filter_chain", "filter.disabled")
