"""Tests for RLSFinalizer — Reasoning-Loop Stopper for Pipeline V2.

Verifies:
1. Observer semantics (ReasoningTextDelta passes through)
2. Exact match loop detection
3. Tool-call disambiguation (same reasoning + different tools = not loop)
4. First reasoning no-loop
5. Intervention payload shape
6. Max attempts exceeded no decision
7. Reset clears buffer
8. Request isolation
9. Conversation history survives recovery reset
10. Observer does not consume reasoning events

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
from keeprollming.filters.reasoning_loop_stopper.stream import RLSFinalizer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_rls(
    max_attempts: int = 3,
) -> RLSFinalizer:
    """Create an RLSFinalizer with common defaults."""
    return RLSFinalizer(
        max_attempts=max_attempts,
    )


def _process_then_finalize(
    rls: RLSFinalizer,
    events: list[StreamEvent],
) -> list[StreamEvent]:
    """Process events then finalize."""
    for ev in events:
        result = rls.process_event(ev)
        assert result == [ev], f"Expected pass-through for {type(ev).__name__}"
    return rls.finalize()


# ---------------------------------------------------------------------------
# A. Observer semantics — events pass through
# ---------------------------------------------------------------------------


def test_rls_observer_does_not_consume_reasoning_events():
    """ReasoningTextDelta must pass through unchanged.

    RLSFinalizer is an observer — it copies events into its buffer for
    detection but returns the original event (not []).
    """
    rls = _make_rls()

    # ReasoningTextDelta
    reasoning = ReasoningTextDelta(delta="I think the answer is 42")
    result = rls.process_event(reasoning)
    assert result == [reasoning], "ReasoningTextDelta must pass through"

    # AssistantTextDelta (non-reasoning event)
    text = AssistantTextDelta(delta="Here is the answer")
    result = rls.process_event(text)
    assert result == [text], "AssistantTextDelta must pass through"

    # ToolCallDelta (non-reasoning event)
    tc = ToolCallDelta(
        index=0,
        id="call_1",
        name="echo",
        arguments_delta='{}',
    )
    result = rls.process_event(tc)
    assert result == [tc], "ToolCallDelta must pass through"

    # Finish (terminal event)
    finish = Finish(reason="stop")
    result = rls.process_event(finish)
    assert result == [finish], "Finish must pass through"


# ---------------------------------------------------------------------------
# B. No loop — passthrough
# ---------------------------------------------------------------------------


def test_rls_no_loop_passthrough():
    """Different reasoning texts should not trigger loop detection.

    Input: ReasoningTextDelta("I think A") + ReasoningTextDelta("Then B")

    Expected: no recovery decision, events pass through.
    """
    rls = _make_rls()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think the answer is A"),
        ReasoningTextDelta(delta="Then I will check B"),
        Finish(reason="stop"),
    ])

    assert rls.decision is None, "No loop should be detected"


def test_rls_first_reasoning_no_loop():
    """First reasoning text should not trigger loop detection.

    Input: ReasoningTextDelta("Let me think...") + Finish

    Expected: no recovery decision (no previous reasoning to compare).
    """
    rls = _make_rls()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="Let me think..."),
        Finish(reason="stop"),
    ])

    assert rls.decision is None
    assert rls.reasoning_buffer == "Let me think..."


# ---------------------------------------------------------------------------
# C. Loop detection
# ---------------------------------------------------------------------------


def test_rls_exact_match_loop():
    """Identical reasoning text across streams should trigger loop detection.

    Input: conversation history contains "I think the answer is" and the
           stream produces the same reasoning.

    Expected: RecoveryDecision with kind="intervention",
    merge_strategy="intervention_specific".
    """
    rls = RLSFinalizer(conversation_reasoning="I think the answer is")

    # Same reasoning as the preceding assistant turn → loop detected.
    events = _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think the answer is"),
        Finish(reason="stop"),
    ])

    assert rls.decision is not None, "Loop should be detected"
    assert rls.decision.kind == "intervention"
    assert rls.decision.merge_strategy == "intervention_specific"
    assert rls.decision.preserve_output_so_far is True
    assert rls.decision.origin_finalizer == "RLSFinalizer"
    assert rls.decision.request_payload_patch is not None

    # Check request_payload_patch shape
    patch = rls.decision.request_payload_patch
    assert "messages" in patch
    messages = patch["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "Your reasoning is repeating" in messages[0]["content"]

    # Check diagnostics
    diag = rls.decision.diagnostics
    assert diag["reasoning_buffer"] == "I think the answer is"
    assert diag["last_reasoning"] == "I think the answer is"


# ---------------------------------------------------------------------------
# D. Tool-call disambiguation
# ---------------------------------------------------------------------------


def test_rls_tool_call_disambiguation():
    """Same reasoning + different tool calls = not a loop.

    Input: ReasoningTextDelta("I think") + ToolCallDelta("echo", {}) +
           ReasoningTextDelta("I think") + ToolCallDelta("search", {})

    Expected: no recovery decision (different tools disambiguate).
    """
    rls = _make_rls()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think the answer is"),
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{}',
        ),
        ReasoningTextDelta(delta=" I think the answer is"),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="search",
            arguments_delta='{}',
        ),
        Finish(reason="stop"),
    ])

    assert rls.decision is None, (
        "Same reasoning with different tool calls should not trigger loop"
    )
    assert rls.has_tool_call is True


# ---------------------------------------------------------------------------
# E. Intervention payload
# ---------------------------------------------------------------------------


def test_rls_intervention_payload():
    """RecoveryDecision request_payload_patch must contain correct shape.

    Expected:
    - messages[0]: user-role nudge message
    """
    rls = RLSFinalizer(conversation_reasoning="I think")

    # Same reasoning as the preceding assistant turn → loop detected.
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think"),
        Finish(reason="stop"),
    ])

    assert rls.decision is not None
    patch = rls.decision.request_payload_patch
    assert patch is not None
    assert "messages" in patch

    messages = patch["messages"]
    assert len(messages) == 1

    # User nudge message
    nudge_msg = messages[0]
    assert nudge_msg["role"] == "user"
    assert "Your reasoning is repeating" in nudge_msg["content"]

    # Decision metadata
    assert rls.decision.kind == "intervention"
    assert rls.decision.merge_strategy == "intervention_specific"
    assert rls.decision.preserve_output_so_far is True
    assert rls.decision.priority == 60


# ---------------------------------------------------------------------------
# F. Max attempts
# ---------------------------------------------------------------------------


def test_rls_max_attempts_exceeded_no_decision():
    """If attempt_index >= max_attempts, no decision should be produced.

    This is C1 behavior — C2 runner integration handles fallback.
    """
    rls = RLSFinalizer(max_attempts=2, conversation_reasoning="I think")
    rls.reset()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think"),
        Finish(reason="stop"),
    ])

    # First attempt should produce a decision (attempt_index=1 after reset, but finalize hasn't incremented yet)
    assert rls.decision is not None
    assert rls.attempt_index == 1

    # Reset for second attempt (attempt_index becomes 2)
    rls.reset()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think"),
        Finish(reason="stop"),
    ])

    # Second attempt should produce a decision (attempt_index=2, but max_attempts=2, so... wait)
    # Actually, the check is attempt_index >= max_attempts, so attempt_index=2 >= 2 should NOT produce a decision
    # Let me fix this test
    assert rls.decision is None
    assert rls.attempt_index == 2


# ---------------------------------------------------------------------------
# G. Reset behavior
# ---------------------------------------------------------------------------


def test_rls_reset_clears_buffer():
    """reset() must clear the local reasoning buffer and detection state.

    After reset, the finalizer should not detect loops from previous
    processing.
    """
    rls = RLSFinalizer(conversation_reasoning="I think")
    rls.reset()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think"),
        Finish(reason="stop"),
    ])

    assert rls.decision is not None
    assert rls.reasoning_buffer == "I think"

    # Reset
    rls.reset()

    # After reset, buffer should be clear
    assert rls.reasoning_buffer == ""

    # New processing with single reasoning should not trigger loop
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="New reasoning"),
        Finish(reason="stop"),
    ])

    assert rls.decision is None


# ---------------------------------------------------------------------------
# H. Conversation isolation
# ---------------------------------------------------------------------------


def test_rls_does_not_compare_unrelated_requests_without_history():
    """Matching reasoning in another finalizer instance is not a loop."""
    first = _make_rls()
    _process_then_finalize(first, [
        ReasoningTextDelta(delta="First reasoning"),
        Finish(reason="stop"),
    ])

    unrelated = _make_rls()
    _process_then_finalize(unrelated, [
        ReasoningTextDelta(delta="First reasoning"),
        Finish(reason="stop"),
    ])

    assert unrelated.decision is None


def test_rls_restores_conversation_reasoning_on_reset():
    """Recovery attempts retain the original request's reasoning history."""
    rls = RLSFinalizer(conversation_reasoning="First reasoning")
    rls.reset()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="First reasoning"),
        Finish(reason="stop"),
    ])
    assert rls.decision is not None


# ---------------------------------------------------------------------------
# I. Finalizer contract conformance
# ---------------------------------------------------------------------------


def test_rls_finalizer_implements_stream_finalizer():
    """RLSFinalizer is an instance of StreamFinalizer."""
    finalizer = RLSFinalizer()
    assert isinstance(finalizer, StreamFinalizer)


def test_rls_finalizer_finalize_idempotent():
    """Second finalize() raises RuntimeError."""
    rls = _make_rls()
    rls.process_event(ReasoningTextDelta(delta="I think"))
    rls.process_event(Finish(reason="stop"))
    rls.finalize()

    with pytest.raises(RuntimeError, match="already called"):
        rls.finalize()


def test_rls_finalizer_priority():
    """RLSFinalizer priority must be 60."""
    rls = RLSFinalizer()
    assert rls.priority == 60


# ---------------------------------------------------------------------------
# K. Experimental within-stream detection (opt-in)
# ---------------------------------------------------------------------------


def test_rls_within_stream_loop_disabled_by_default():
    """Within-stream detection is disabled by default.

    Input: Two identical reasoning deltas in one stream (no cache).

    Expected: no recovery decision (within-stream detection disabled).
    """
    rls = _make_rls()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think the answer is"),
        ReasoningTextDelta(delta=" I think the answer is"),
        Finish(reason="stop"),
    ])

    assert rls.decision is None, (
        "Within-stream detection should be disabled by default"
    )


def test_rls_within_stream_loop_enabled():
    """Within-stream detection works when explicitly enabled.

    Input: Two identical reasoning deltas in one stream (no cache).
    Enabled via detect_within_stream_loop=True.

    Expected: RecoveryDecision with kind="intervention".
    """
    rls = RLSFinalizer(detect_within_stream_loop=True)
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think the answer is"),
        ReasoningTextDelta(delta=" I think the answer is"),
        Finish(reason="stop"),
    ])

    assert rls.decision is not None, (
        "Within-stream loop should be detected when enabled"
    )
    assert rls.decision.kind == "intervention"
    assert rls.decision.merge_strategy == "intervention_specific"


def test_rls_within_stream_loop_with_tool_disambiguation():
    """Within-stream detection respects tool-call disambiguation.

    Input: Same reasoning + different tool calls in one stream.
    Enabled via detect_within_stream_loop=True.

    Expected: no recovery decision (tool calls differ).
    """
    rls = RLSFinalizer(detect_within_stream_loop=True)
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta="I think the answer is"),
        ToolCallDelta(
            index=0,
            id="call_1",
            name="echo",
            arguments_delta='{}',
        ),
        ReasoningTextDelta(delta=" I think the answer is"),
        ToolCallDelta(
            index=1,
            id="call_2",
            name="search",
            arguments_delta='{}',
        ),
        Finish(reason="stop"),
    ])

    assert rls.decision is None, (
        "Same reasoning with different tool calls should not trigger loop"
    )


# ---------------------------------------------------------------------------
# J. Edge cases
# ---------------------------------------------------------------------------


def test_rls_empty_reasoning_no_crash():
    """Empty reasoning text should not crash.

    Expected: no recovery decision.
    """
    rls = _make_rls()
    _process_then_finalize(rls, [
        ReasoningTextDelta(delta=""),
        Finish(reason="stop"),
    ])

    assert rls.decision is None
    assert rls.reasoning_buffer == ""


def test_rls_finish_only_no_crash():
    """Input only Finish — should not crash.

    Expected: no recovery decision.
    """
    rls = _make_rls()
    _process_then_finalize(rls, [
        Finish(reason="stop"),
    ])

    assert rls.decision is None
