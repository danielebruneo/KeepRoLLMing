"""Tests for TLSFinalizer — Tool-Loop Stopper for Pipeline V2.

Verifies:
1. Observer semantics (ToolCallDelta/ToolCallComplete pass through)
2. Exact consecutive loop detection
3. Fuzzy loop detection
4. AB-loop detection
5. No-tool-calls passthrough
6. Same name different args no-loop
7. Intervention payload shape
8. Max attempts exceeded no decision
9. Reset clears buffer
10. Observer does not consume tool events

This module is independent of the V2 runner — it tests the finalizer
unit contract only.
"""

from __future__ import annotations

from typing import List

import pytest

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Finish,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)
from keeprollming.streaming.finalizers import StreamFinalizer
from keeprollming.filters.tool_loop_stopper.stream import TLSFinalizer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tls(
    max_attempts: int = 3,
    fuzzy_threshold: float = None,
    detect_ab_loop: bool = False,
) -> TLSFinalizer:
    """Create a TLSFinalizer with common defaults."""
    return TLSFinalizer(
        max_attempts=max_attempts,
        fuzzy_threshold=fuzzy_threshold,
        detect_ab_loop=detect_ab_loop,
    )


def _process_then_finalize(
    tls: TLSFinalizer,
    events: list[StreamEvent],
) -> list[StreamEvent]:
    """Process events then finalize."""
    for ev in events:
        result = tls.process_event(ev)
        assert result == [ev], f"Expected pass-through for {type(ev).__name__}"
    return tls.finalize()


# ---------------------------------------------------------------------------
# A. Observer semantics — events pass through
# ---------------------------------------------------------------------------


def test_tls_observer_does_not_consume_tool_events():
    """ToolCallDelta and ToolCallComplete must pass through unchanged.

    TLSFinalizer is an observer — it copies events into its buffer for
    detection but returns the original event (not []).
    """
    tls = _make_tls()

    # ToolCallDelta
    tc_delta = ToolCallDelta(
        index=0,
        id="call_1",
        name="echo",
        arguments_delta='{"message": "hello"}',
    )
    result = tls.process_event(tc_delta)
    assert result == [tc_delta], "ToolCallDelta must pass through"

    # ToolCallComplete
    tc_complete = ToolCallComplete(
        index=0,
        id="call_1",
        name="echo",
        arguments_json='{"message": "hello"}',
        arguments_obj={"message": "hello"},
    )
    result = tls.process_event(tc_complete)
    assert result == [tc_complete], "ToolCallComplete must pass through"

    # AssistantTextDelta (non-tool event)
    text = AssistantTextDelta(delta="some text")
    result = tls.process_event(text)
    assert result == [text], "AssistantTextDelta must pass through"

    # ReasoningTextDelta (non-tool event)
    reasoning = ReasoningTextDelta(delta="thinking...")
    result = tls.process_event(reasoning)
    assert result == [reasoning], "ReasoningTextDelta must pass through"

    # Finish (terminal event)
    finish = Finish(reason="stop")
    result = tls.process_event(finish)
    assert result == [finish], "Finish must pass through"


# ---------------------------------------------------------------------------
# B. No loop — passthrough
# ---------------------------------------------------------------------------


def test_tls_no_loop_passthrough():
    """Different tool calls should not trigger loop detection.

    Input: ToolCallDelta("echo", {"msg": "a"}) + ToolCallDelta("search", {"q": "b"})

    Expected: no recovery decision, events pass through.
    """
    tls = _make_tls()
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{"msg": "a"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="search",
            arguments_delta='{"q": "b"}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is None, "No loop should be detected"
    assert tls.has_tool_call is True


def test_tls_no_tool_calls_passthrough():
    """No tool calls at all — should not trigger loop detection.

    Input: AssistantTextDelta + Finish

    Expected: no recovery decision.
    """
    tls = _make_tls()
    _process_then_finalize(tls, [
        AssistantTextDelta(delta="Here is the answer."),
        Finish(reason="stop"),
    ])

    assert tls.decision is None
    assert tls.has_tool_call is False


def test_tls_same_name_different_args_no_loop():
    """Same tool name but different arguments — not a loop.

    Input: ToolCallDelta("echo", {"msg": "a"}) + ToolCallDelta("echo", {"msg": "b"})

    Expected: no recovery decision (different signatures).
    """
    tls = _make_tls()
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{"msg": "a"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="echo",
            arguments_delta='{"msg": "b"}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is None, (
        "Same name with different args should not trigger loop"
    )
    assert tls.has_tool_call is True


# ---------------------------------------------------------------------------
# C. Loop detection
# ---------------------------------------------------------------------------


def test_tls_exact_consecutive_loop():
    """Identical consecutive tool call signatures should trigger loop detection.

    Input: ToolCallDelta("echo", {"msg": "hello"}) + ToolCallDelta("echo", {"msg": "hello"})

    Expected: RecoveryDecision with kind="intervention",
    merge_strategy="inject_tool_result".
    """
    tls = _make_tls()
    events = _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is not None, "Loop should be detected"
    assert tls.decision.kind == "intervention"
    assert tls.decision.merge_strategy == "inject_tool_result"
    assert tls.decision.preserve_output_so_far is True
    assert tls.decision.origin_finalizer == "TLSFinalizer"
    assert tls.decision.request_payload_patch is not None

    # Check request_payload_patch shape
    patch = tls.decision.request_payload_patch
    assert "messages" in patch
    messages = patch["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "tool"
    assert messages[0]["tool_call_id"] == "call_2"
    assert "Tool result:" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Do NOT call" in messages[1]["content"]

    # Check diagnostics
    diag = tls.decision.diagnostics
    assert diag["loop_type"] == "exact_consecutive"
    assert diag["tool_name"] == "echo"


def test_tls_fuzzy_loop():
    """Fuzzy-matching tool call signatures should trigger loop detection.

    Input: ToolCallDelta("echo", {"msg": "hello world"}) +
           ToolCallDelta("echo", {"msg": "hello wrold"}) (typo in args)

    Expected: RecoveryDecision with kind="intervention".
    """
    tls = _make_tls(fuzzy_threshold=0.8)
    events = _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{"msg": "hello world"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="echo",
            arguments_delta='{"msg": "hello wrold"}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is not None, "Fuzzy loop should be detected"
    assert tls.decision.kind == "intervention"
    assert tls.decision.diagnostics["loop_type"] == "fuzzy"


def test_tls_ab_loop():
    """AB-loop pattern (alternating two distinct signatures) should trigger detection.

    Input: ToolCallDelta("echo", {}) + ToolCallDelta("search", {}) +
           ToolCallDelta("echo", {}) + ToolCallDelta("search", {})

    Expected: RecoveryDecision with kind="intervention".
    """
    tls = _make_tls(detect_ab_loop=True)
    events = _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="search",
            arguments_delta='{}',
        ),
        ToolCallDelta(
            index=2,
            id="call_3",
            name="echo",
            arguments_delta='{}',
        ),
        ToolCallDelta(
            index=3,
            id="call_4",
            name="search",
            arguments_delta='{}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is not None, "AB-loop should be detected"
    assert tls.decision.kind == "intervention"
    assert tls.decision.diagnostics["loop_type"] == "ab_loop"


# ---------------------------------------------------------------------------
# D. Intervention payload
# ---------------------------------------------------------------------------


def test_tls_intervention_payload():
    """RecoveryDecision request_payload_patch must contain correct shape.

    Expected:
    - messages[0]: tool-role message with tool_call_id and tls_message
    - messages[1]: user-role nudge message
    """
    tls = _make_tls()
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_loop_1",
            name="get_weather",
            arguments_delta='{"city": "London"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_loop_2",
            name="get_weather",
            arguments_delta='{"city": "London"}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is not None
    patch = tls.decision.request_payload_patch
    assert patch is not None
    assert "messages" in patch

    messages = patch["messages"]
    assert len(messages) == 2

    # Tool result message
    tool_msg = messages[0]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "call_loop_2"
    assert "get_weather" in tool_msg["content"]

    # User nudge message
    nudge_msg = messages[1]
    assert nudge_msg["role"] == "user"
    assert "Do NOT call" in nudge_msg["content"]
    assert "get_weather" in nudge_msg["content"]

    # Decision metadata
    assert tls.decision.kind == "intervention"
    assert tls.decision.merge_strategy == "inject_tool_result"
    assert tls.decision.preserve_output_so_far is True
    assert tls.decision.priority == 55


# ---------------------------------------------------------------------------
# E. Max attempts
# ---------------------------------------------------------------------------


def test_tls_max_attempts_exceeded_no_decision():
    """If attempt_index >= max_attempts, no decision should be produced.

    This is C1 behavior — C2 runner integration handles fallback.
    """
    tls = _make_tls(max_attempts=2)

    # First attempt: process events, finalize (attempt_index=0)
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        Finish(reason="stop"),
    ])

    # First attempt should produce a decision
    assert tls.decision is not None
    assert tls.attempt_index == 0

    # Reset for second attempt (attempt_index becomes 1)
    tls.reset()
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_3",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_4",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        Finish(reason="stop"),
    ])

    # Second attempt should produce a decision (attempt_index=1 < max_attempts=2)
    assert tls.decision is not None
    assert tls.attempt_index == 1

    # Reset for third attempt (attempt_index becomes 2)
    tls.reset()
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_5",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_6",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        Finish(reason="stop"),
    ])

    # Third attempt: attempt_index=2 >= max_attempts=2, no decision
    assert tls.decision is None
    assert tls.attempt_index == 2


# ---------------------------------------------------------------------------
# F. Reset behavior
# ---------------------------------------------------------------------------


def test_tls_reset_clears_buffer():
    """reset() must clear the tool buffer and detection state.

    After reset, the finalizer should not detect loops from previous
    processing.
    """
    tls = _make_tls()

    # First processing: create a loop
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is not None
    assert len(tls.tool_signatures) == 2

    # Reset
    tls.reset()

    # After reset, buffer should be clear
    assert len(tls.tool_signatures) == 0
    assert tls.has_tool_call is False

    # New processing with single tool call should not trigger loop
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_3",
            name="echo",
            arguments_delta='{"msg": "hello"}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is None


# ---------------------------------------------------------------------------
# G. Finalizer contract conformance
# ---------------------------------------------------------------------------


def test_tls_finalizer_implements_stream_finalizer():
    """TLSFinalizer is an instance of StreamFinalizer."""
    finalizer = TLSFinalizer()
    assert isinstance(finalizer, StreamFinalizer)


def test_tls_finalizer_finalize_idempotent():
    """Second finalize() raises RuntimeError."""
    tls = _make_tls()
    tls.process_event(ToolCallDelta(
        index=0,
        id="call_1",
        name="echo",
        arguments_delta='{}',
    ))
    tls.process_event(Finish(reason="stop"))
    tls.finalize()

    with pytest.raises(RuntimeError, match="already called"):
        tls.finalize()


def test_tls_finalizer_priority():
    """TLSFinalizer priority must be 55."""
    tls = TLSFinalizer()
    assert tls.priority == 55


# ---------------------------------------------------------------------------
# H. Edge cases
# ---------------------------------------------------------------------------


def test_tls_single_tool_call_no_loop():
    """Single tool call should not trigger loop detection.

    Input: ToolCallDelta("echo", {}) + Finish

    Expected: no recovery decision.
    """
    tls = _make_tls()
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is None
    assert tls.has_tool_call is True


def test_tls_finish_only_no_crash():
    """Input only Finish — should not crash.

    Expected: no recovery decision.
    """
    tls = _make_tls()
    _process_then_finalize(tls, [
        Finish(reason="stop"),
    ])

    assert tls.decision is None
    assert tls.has_tool_call is False


def test_tls_empty_tool_call_no_crash():
    """ToolCallDelta with empty name — should not crash.

    Expected: no recovery decision.
    """
    tls = _make_tls()
    _process_then_finalize(tls, [
        ToolCallDelta(
            index=0,
            id="call_1",
            name="",
            arguments_delta='{}',
        ),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="",
            arguments_delta='{}',
        ),
        Finish(reason="stop"),
    ])

    assert tls.decision is None
