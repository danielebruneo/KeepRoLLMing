"""Construct the stream-finalizer chain from route configuration.

Streaming has its own protocol and execution model.  It therefore consumes
the route's declarative filter settings directly instead of inspecting
request/response filter objects.  Those objects remain responsible for their
respective non-streaming phases.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from keeprollming.filters import built_in_filter_modules
from keeprollming.streaming.finalizers import StreamFinalizer, ToolCallFinalizer
from keeprollming.filters.nudge.stream import (
    NudgeContinuationFinalizer,
)
from keeprollming.filters.reasoning_loop_stopper.stream import RLSFinalizer
from keeprollming.filters.timestamp.stream import TimestampFinalizer
from keeprollming.filters.tool_loop_stopper.stream import TLSFinalizer
from keeprollming.filters.tool_rewrite.stream import ToolRewriteFinalizer

_DEFAULT_TS_TEMPLATE = "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"
_DEFAULT_NUDGE_MESSAGE = "Continue."
_DEFAULT_MAX_ATTEMPTS = 3


def _enabled(config: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    """Return an enabled finalizer configuration, otherwise ``None``."""
    value = config.get(name)
    if not isinstance(value, Mapping) or value.get("enabled") is not True:
        return None
    return value


def _conversation_context(
    messages: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[Mapping[str, Any]], str | None]:
    """Extract the history loop finalizers need from OpenAI messages."""
    tool_calls: list[Mapping[str, Any]] = []
    last_reasoning: str | None = None
    for message in messages or ():
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            continue
        raw_calls = message.get("tool_calls")
        if isinstance(raw_calls, list):
            tool_calls.extend(call for call in raw_calls if isinstance(call, Mapping))
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            last_reasoning = reasoning
    return tool_calls, last_reasoning


def build_finalizers(
    stream_filter_config: Mapping[str, Any] | None,
    conversation_messages: Sequence[Mapping[str, Any]] | None = None,
) -> list[StreamFinalizer]:
    """Build the finalizer chain from enabled route settings.

    ``stream_filter_config`` is the normalized route ``filters`` mapping:
    ``{filter_name: {enabled: bool, ...}}``. No finalizer reads mutable
    request-filter state, so streaming configuration can be tested and evolved
    independently from request and non-streaming response filters.
    """
    config = stream_filter_config or {}
    conversation_tool_calls, conversation_reasoning = _conversation_context(
        conversation_messages
    )
    finalizers: list[StreamFinalizer] = [ToolCallFinalizer(flush_valid_only=True)]

    timestamp = _enabled(config, "timestamp")
    if timestamp is not None:
        finalizers.append(
            TimestampFinalizer(
                template=timestamp.get("template", _DEFAULT_TS_TEMPLATE),
                timezone=timestamp.get("timezone", "UTC"),
                tail_buffer_size=timestamp.get("tail_buffer_size", 1024),
            )
        )

    tool_rewrite = _enabled(config, "tool_rewrite")
    if tool_rewrite is not None:
        finalizers.append(
            ToolRewriteFinalizer(
                supported_patterns=tool_rewrite.get(
                    "supported_patterns", ["nested", "separate", "function"],
                ),
            )
        )

    nudge = _enabled(config, "model_nudge")
    if nudge is not None:
        finalizers.append(
            NudgeContinuationFinalizer(
                trigger_patterns=nudge.get("trigger_patterns", [":$"]),
                nudge_message=nudge.get("nudge_message", _DEFAULT_NUDGE_MESSAGE),
                max_attempts=nudge.get("max_attempts", _DEFAULT_MAX_ATTEMPTS),
                tail_buffer_size=nudge.get("tail_buffer_size", 1024),
                stream_deltas=True,
            )
        )

    tls = _enabled(config, "model_tool_loop_stopper")
    if tls is not None:
        finalizers.append(
            TLSFinalizer(
                max_attempts=tls.get("max_attempts", tls.get("max_repeats", _DEFAULT_MAX_ATTEMPTS)),
                fuzzy_threshold=tls.get("fuzzy_threshold"),
                detect_ab_loop=tls.get("ab_loop_detection", False),
                tls_message=tls.get(
                    "tls_message",
                    "Tool result: please provide a direct answer without calling tools.",
                ),
                nudge_message=(
                    tls.get("nudge_message", "")
                    if tls.get("send_user_message", True) is False
                    else tls.get(
                        "nudge_message",
                        "Do NOT call the tool again. Provide a direct answer.",
                    )
                ),
                fallback_message=tls.get(
                    "fallback_streaming_message",
                    tls.get("fallback_message"),
                ),
                conversation_tool_calls=conversation_tool_calls,
            )
        )

    rls = _enabled(config, "reasoning_loop_stopper")
    if rls is not None:
        finalizers.append(
            RLSFinalizer(
                max_attempts=rls.get("max_attempts", rls.get("max_repeats", _DEFAULT_MAX_ATTEMPTS)),
                nudge_message=rls.get(
                    "rls_message",
                    "Your reasoning is repeating. Think differently or provide a direct answer.",
                ),
                detect_within_stream_loop=rls.get("detect_within_stream_loop", False),
                conversation_reasoning=conversation_reasoning,
                conversation_tool_calls=conversation_tool_calls,
                fallback_message=rls.get(
                    "fallback_streaming_message",
                    rls.get("fallback_message"),
                ),
            )
        )

    modules = built_in_filter_modules()
    finalizer_names = {
        TimestampFinalizer: "timestamp",
        ToolRewriteFinalizer: "tool_rewrite",
        NudgeContinuationFinalizer: "model_nudge",
        TLSFinalizer: "model_tool_loop_stopper",
        RLSFinalizer: "reasoning_loop_stopper",
    }
    for finalizer in finalizers:
        name = finalizer_names.get(type(finalizer))
        if name is not None:
            module = modules[name]
            module_config = config.get(name, {})
            # A route-local priority is a deliberate override of the module's
            # phase default. When it is absent, retain the module's independent
            # streaming priority rather than inheriting request ordering.
            finalizer.priority = (
                module_config["priority"]
                if isinstance(module_config, Mapping)
                and "priority" in module_config
                else module.stream_priority
            )

    return sorted(finalizers, key=lambda finalizer: finalizer.priority)
