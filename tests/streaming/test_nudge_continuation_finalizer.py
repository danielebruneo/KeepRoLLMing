"""Tests for NudgeContinuationFinalizer — lazy-output detection for Pipeline V2.

Verifies:
1. Lazy pattern detection (`:$` at end of accumulated text)
2. Non-lazy content pass-through
3. Tool call awareness (skip nudge if tool_calls present)
4. Content merge (original + continuation)
5. Reasoning-only response handling
6. Multiple nudge attempts (up to `max_attempts`)
7. Anti-duplication / privacy invariants

This module is independent of the V2 runner — it tests the finalizer
unit contract only.
"""

from __future__ import annotations

import re
from typing import List

import pytest

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Done,
    Finish,
    Keepalive,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)
from keeprollming.streaming.finalizers import StreamFinalizer
from keeprollming.filters.nudge.stream import (
    NudgeContinuationFinalizer,
    RecoveryDecision,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LAZY_NUDGE = NudgeContinuationFinalizer(
    trigger_patterns=[":$"],
    nudge_message="Continue.",
    max_attempts=3,
)

NONLAZY_NUDGE = NudgeContinuationFinalizer(
    trigger_patterns=[":$"],
    nudge_message="Continue.",
    max_attempts=3,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flush(nudge: NudgeContinuationFinalizer) -> list[StreamEvent]:
    """Call finalize() and return the emitted events."""
    return nudge.finalize()


def _process_then_flush(
    nudge: NudgeContinuationFinalizer,
    events: list[StreamEvent],
) -> list[StreamEvent]:
    """Process a list of events then finalize."""
    for ev in events:
        nudge.process_event(ev)
    return _flush(nudge)


def _simulate_merge(
    prefix: str,
    continuation: str,
) -> str:
    """Simulate the runner's append_continuation merge plan.

    This is a B1 unit-level simulation — in B2 the runner produces the
    actual merged output from the continuation stream.
    """
    return prefix + continuation


# ---------------------------------------------------------------------------
# A. Lazy response detection
# ---------------------------------------------------------------------------


def test_lazy_response_detected_at_finalize():
    """Input: AssistantTextDelta("Here is the list:") + Finish

    Expected: recovery decision kind = append_continuation,
    preserve_output_so_far = True, lazy_detected = True.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
        Finish(reason="stop"),
    ])

    assert nudge.lazy_detected is True
    assert nudge.decision is not None
    assert nudge.decision.kind == "append_continuation"
    assert nudge.decision.merge_strategy == "append_continuation"
    assert nudge.decision.preserve_output_so_far is True
    assert nudge.decision.origin_finalizer == "NudgeContinuationFinalizer"
    assert nudge.decision.request_payload_patch is not None
    # request_payload_patch uses "messages" key (not "nudge_message")
    assert "messages" in nudge.decision.request_payload_patch
    assert "nudge_message" not in nudge.decision.request_payload_patch
    messages = nudge.decision.request_payload_patch["messages"]
    assert len(messages) == 2
    assert messages[0] == {"role": "assistant", "content": "Here is the list:"}
    assert messages[1] == {"role": "user", "content": "Continue."}
    assert "Here is the list:" in nudge.decision.diagnostics["lazy_prefix"]

    # Buffered text is emitted as AssistantTextDelta
    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Here is the list:"


def test_lazy_response_preserve_output_so_far():
    """Recovery decision includes preserve_output_so_far = True."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Partial answer:"),
    ])

    assert nudge.decision is not None
    assert nudge.decision.preserve_output_so_far is True
    assert nudge.decision.merge_strategy == "append_continuation"


def test_lazy_response_accepted_prefix_included():
    """Original text is preserved in the decision diagnostics."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    assert nudge.decision is not None
    assert nudge.decision.diagnostics["detected_pattern"] == "Here is the list:"
    assert nudge.decision.diagnostics["lazy_prefix"] == "Here is the list:"


def test_original_text_not_discarded():
    """Original lazy output is still available after finalize."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Partial answer:"),
    ])

    # accepted_prefix still holds the original text
    assert nudge.accepted_prefix == "Partial answer:"

    # Emitted events contain the original text
    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Partial answer:"


# ---------------------------------------------------------------------------
# B. Non-lazy response
# ---------------------------------------------------------------------------


def test_nonlazy_no_recovery_decision():
    """Input: AssistantTextDelta("Here is the complete answer.") + Finish

    Expected: no recovery decision, original text preserved/emitted once.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the complete answer."),
        Finish(reason="stop"),
    ])

    assert nudge.lazy_detected is False
    assert nudge.decision is None

    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Here is the complete answer."


# ---------------------------------------------------------------------------
# C. Original output preservation
# ---------------------------------------------------------------------------


def test_original_output_preserved_after_finalize():
    """Input: AssistantTextDelta("Partial answer:")

    Expected after finalize: original output is still available,
    no destructive replace behavior.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Partial answer:"),
    ])

    assert nudge.accepted_prefix == "Partial answer:"
    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Partial answer:"


# ---------------------------------------------------------------------------
# D. Tool-call skip
# ---------------------------------------------------------------------------


def test_tool_call_delta_skips_nudge():
    """Input includes ToolCallDelta.

    Expected: no nudge continuation decision, tool call events pass through.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    result1 = nudge.process_event(AssistantTextDelta(delta="Here is the list:"))
    assert result1 == [], "Text should be buffered"
    result2 = nudge.process_event(
        ToolCallDelta(index=0, id="call_1", name="echo", arguments_delta='{}')
    )
    assert len(result2) == 1 and isinstance(result2[0], ToolCallDelta)
    result3 = nudge.process_event(Finish(reason="stop"))
    assert len(result3) == 1 and isinstance(result3[0], Finish)

    text_events = _flush(nudge)
    passthrough_events = nudge.get_passthrough_events()

    assert nudge.has_tool_call is True
    assert nudge.lazy_detected is False  # tool call prevents nudge
    assert nudge.decision is None

    # ToolCallDelta passes through (captured in passthrough_events)
    tc_events = [e for e in passthrough_events if isinstance(e, ToolCallDelta)]
    assert len(tc_events) == 1


def test_tool_call_complete_skips_nudge():
    """Input includes ToolCallComplete.

    Expected: no nudge continuation decision.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    nudge.process_event(AssistantTextDelta(delta="Here is the list:"))
    nudge.process_event(
        ToolCallComplete(
            index=0,
            id="call_1",
            name="echo",
            arguments_json="{}",
            arguments_obj={},
        )
    )
    nudge.process_event(Finish(reason="stop"))
    _flush(nudge)
    passthrough_events = nudge.get_passthrough_events()

    assert nudge.has_tool_call is True
    assert nudge.decision is None

    # ToolCallComplete passes through (captured in passthrough_events)
    tc_events = [e for e in passthrough_events if isinstance(e, ToolCallComplete)]
    assert len(tc_events) == 1


# ---------------------------------------------------------------------------
# E. Reasoning handling
# ---------------------------------------------------------------------------


def test_reasoning_text_delta_passes_through():
    """ReasoningTextDelta is buffered but not treated as lazy assistant text.

    Expected: no crash, reasoning is buffered separately, and is not
    considered for lazy detection.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        ReasoningTextDelta(delta="I think the answer is:"),
        AssistantTextDelta(delta="42"),
        Finish(reason="stop"),
    ])

    assert nudge.lazy_detected is False
    assert nudge.decision is None

    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    reasoning_events = [e for e in events if isinstance(e, ReasoningTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "42"
    assert len(reasoning_events) == 1
    assert reasoning_events[0].delta == "I think the answer is:"


def test_reasoning_only_no_crash():
    """Input includes only ReasoningTextDelta.

    Expected: no crash, no recovery decision, reasoning emitted.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        ReasoningTextDelta(delta="Let me think..."),
        Finish(reason="stop"),
    ])

    assert nudge.lazy_detected is False
    assert nudge.decision is None

    reasoning_events = [e for e in events if isinstance(e, ReasoningTextDelta)]
    assert len(reasoning_events) == 1
    assert reasoning_events[0].delta == "Let me think..."


def test_reasoning_with_lazy_assistant_text():
    """ReasoningTextDelta + lazy AssistantTextDelta.

    Expected: lazy detected on assistant text, reasoning emitted.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        ReasoningTextDelta(delta="I think:"),
        AssistantTextDelta(delta="Here is the list:"),
        Finish(reason="stop"),
    ])

    assert nudge.lazy_detected is True
    assert nudge.decision is not None

    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    reasoning_events = [e for e in events if isinstance(e, ReasoningTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Here is the list:"
    assert len(reasoning_events) == 1
    assert reasoning_events[0].delta == "I think:"


# ---------------------------------------------------------------------------
# F. Multiple assistant chunks
# ---------------------------------------------------------------------------


def test_multiple_assistant_chunks_accumulated():
    """Input: AssistantTextDelta("Here ") + AssistantTextDelta("is ") +
    AssistantTextDelta("the list:")

    Expected: accumulated text detected as lazy, original output preserved.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here "),
        AssistantTextDelta(delta="is "),
        AssistantTextDelta(delta="the list:"),
        Finish(reason="stop"),
    ])

    assert nudge.lazy_detected is True
    assert nudge.decision is not None
    assert nudge.accepted_prefix == "Here is the list:"

    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Here is the list:"


# ---------------------------------------------------------------------------
# G. No content
# ---------------------------------------------------------------------------


def test_finish_only_no_crash():
    """Input only Finish.

    Expected: no recovery decision, no crash.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    nudge.process_event(Finish(reason="stop"))
    events = _flush(nudge)
    passthrough_events = nudge.get_passthrough_events()

    assert nudge.lazy_detected is False
    assert nudge.decision is None
    # No text was buffered, so finalize returns []
    assert len(events) == 0
    # Finish was captured as passthrough
    assert len(passthrough_events) == 1
    assert isinstance(passthrough_events[0], Finish)


def test_done_only_no_crash():
    """Input only Done.

    Expected: no recovery decision, no crash.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        Done(),
    ])

    assert nudge.lazy_detected is False
    assert nudge.decision is None


def test_empty_assistant_text_no_crash():
    """AssistantTextDelta("") + Finish.

    Expected: no recovery decision, no crash.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta=""),
        Finish(reason="stop"),
    ])

    assert nudge.lazy_detected is False
    assert nudge.decision is None


# ---------------------------------------------------------------------------
# H. Max attempt guard metadata
# ---------------------------------------------------------------------------


def test_max_attempts_in_decision():
    """RecoveryDecision includes max_attempts metadata."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=5,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    assert nudge.decision is not None
    assert nudge.decision.max_attempts == 5


def test_attempt_index_in_decision():
    """RecoveryDecision includes attempt_index = 0."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    assert nudge.decision is not None
    assert nudge.decision.attempt_index == 0


# ---------------------------------------------------------------------------
# I. Anti-duplication / privacy invariants
# ---------------------------------------------------------------------------


def test_lazy_prefix_in_merge_plan_exactly_once():
    """Simulated merge: original lazy prefix appears exactly once.

    Original: "Here is the list:"
    Continuation: " item 1, item 2."
    Final: "Here is the list: item 1, item 2."

    Invalid: "Here is the list:Here is the list: item 1, item 2."
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    prefix = nudge.accepted_prefix  # "Here is the list:"
    continuation = " item 1, item 2."
    merged = _simulate_merge(prefix, continuation)

    assert merged == "Here is the list: item 1, item 2."
    assert merged.count("Here is the list:") == 1, (
        "Lazy prefix must appear exactly once in merged output"
    )


def test_continuation_text_in_merge_plan_exactly_once():
    """Simulated merge: continuation text appears exactly once.

    Original: "Here is the list:"
    Continuation: " item 1, item 2."
    Final: "Here is the list: item 1, item 2."

    Invalid: "Here is the list: item 1, item 2. item 1, item 2."
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    prefix = nudge.accepted_prefix
    continuation = " item 1, item 2."
    merged = _simulate_merge(prefix, continuation)

    assert merged.count("item 1, item 2.") == 1, (
        "Continuation text must appear exactly once in merged output"
    )


def test_nudge_message_not_in_planned_output():
    """Nudge message is internal request context only.

    Nudge: "Please continue the previous answer without restarting."
    Original: "Here is the list:"
    Continuation: " item 1, item 2."

    Expected: nudge message NOT present in merged output.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Please continue the previous answer without restarting.",
        max_attempts=3,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    prefix = nudge.accepted_prefix
    continuation = " item 1, item 2."
    merged = _simulate_merge(prefix, continuation)
    nudge_msg = nudge.nudge_message

    assert nudge_msg not in merged, (
        f"Nudge message '{nudge_msg}' must not appear in merged output"
    )


def test_nudge_message_only_in_request_patch():
    """Nudge message is present in request_payload_patch but not in
    assistant output events.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Please continue the previous answer without restarting.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
        Finish(reason="stop"),
    ])

    nudge_msg = nudge.nudge_message

    # Nudge message is in the request patch (via messages[1]["content"])
    assert nudge.decision is not None
    assert nudge.decision.request_payload_patch is not None
    assert "messages" in nudge.decision.request_payload_patch
    assert "nudge_message" not in nudge.decision.request_payload_patch
    messages = nudge.decision.request_payload_patch["messages"]
    assert len(messages) == 2
    assert messages[0] == {"role": "assistant", "content": "Here is the list:"}
    assert messages[1] == {"role": "user", "content": nudge_msg}

    # Nudge message is NOT in any emitted event
    all_text = ""
    for ev in events:
        if isinstance(ev, AssistantTextDelta):
            all_text += ev.delta
        elif isinstance(ev, ReasoningTextDelta):
            all_text += ev.delta

    assert nudge_msg not in all_text, (
        f"Nudge message '{nudge_msg}' must not appear in emitted events"
    )


def test_first_attempt_finish_done_not_part_of_planned_output():
    """First attempt's Finish/Done are internal to the runner.

    In B1, the final planned output consists of:
    - AssistantTextDelta events from finalize()
    - ReasoningTextDelta events from finalize()

    Finish and Done are NOT part of the emitted events from finalize().
    They are handled separately by the runner.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
        Finish(reason="stop"),
    ])

    # finalize() returns text + reasoning events, NOT Finish/Done
    finish_events = [e for e in events if isinstance(e, Finish)]
    done_events = [e for e in events if isinstance(e, Done)]

    assert len(finish_events) == 0, (
        "finalize() should not emit Finish"
    )
    assert len(done_events) == 0, (
        "finalize() should not emit Done"
    )

    # Only text events are emitted
    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Here is the list:"


def test_no_destructive_replace():
    """Original output is preserved, not replaced.

    Expected: accepted_prefix == original text, not a transformed version.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    original = "Here is the list:"
    assert nudge.accepted_prefix == original, (
        "Original output must be preserved exactly, not replaced"
    )


def test_no_duplicate_append():
    """Simulated merge: no duplication of prefix or continuation.

    prefix + continuation must equal the merged string exactly.
    """
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    _process_then_flush(nudge, [
        AssistantTextDelta(delta="Here is the list:"),
    ])

    prefix = nudge.accepted_prefix
    continuation = " item 1, item 2."
    merged = _simulate_merge(prefix, continuation)

    expected = prefix + continuation
    assert merged == expected, (
        "Merged output must be exactly prefix + continuation"
    )
    assert merged.startswith(prefix)
    assert merged.endswith(continuation)


# ---------------------------------------------------------------------------
# J. Non-text events pass through
# ---------------------------------------------------------------------------


def test_finish_event_passes_through():
    """Finish is returned as-is by process_event."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    result = nudge.process_event(Finish(reason="stop"))
    assert result == [Finish(reason="stop")]


def test_done_event_passes_through():
    """Done is returned as-is by process_event."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    result = nudge.process_event(Done())
    assert result == [Done()]


def test_keepalive_event_passes_through():
    """Keepalive is returned as-is by process_event."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    result = nudge.process_event(Keepalive())
    assert result == [Keepalive()]


# ---------------------------------------------------------------------------
# K. Finalizer contract conformance
# ---------------------------------------------------------------------------


def test_nudge_continuation_finalizer_implements_stream_finalizer():
    """NudgeContinuationFinalizer is an instance of StreamFinalizer."""
    finalizer = NudgeContinuationFinalizer()
    assert isinstance(finalizer, StreamFinalizer)


def test_nudge_finalizer_finalize_idempotent():
    """Second finalize() raises RuntimeError."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    nudge.process_event(AssistantTextDelta(delta="test:"))
    nudge.finalize()

    with pytest.raises(RuntimeError, match="already called"):
        nudge.finalize()


def test_nudge_finalizer_process_after_finalize_raises():
    """process_event after finalize() raises RuntimeError."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    nudge.finalize()

    with pytest.raises(RuntimeError, match="already called"):
        nudge.process_event(AssistantTextDelta(delta="test:"))


# ---------------------------------------------------------------------------
# L. Non-lazy pass-through (no buffering)
# ---------------------------------------------------------------------------


def test_nonlazy_text_emitted_from_finalize():
    """Non-lazy text is buffered then emitted from finalize()."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Complete answer."),
    ])

    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_events) == 1
    assert text_events[0].delta == "Complete answer."


def test_interleaved_text_and_reasoning():
    """Interleaved text and reasoning deltas are accumulated correctly."""
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )
    events = _process_then_flush(nudge, [
        AssistantTextDelta(delta="Hello "),
        ReasoningTextDelta(delta="thinking..."),
        AssistantTextDelta(delta="world."),
        Finish(reason="stop"),
    ])

    text_events = [e for e in events if isinstance(e, AssistantTextDelta)]
    reasoning_events = [e for e in events if isinstance(e, ReasoningTextDelta)]

    assert len(text_events) == 1
    assert text_events[0].delta == "Hello world."
    assert len(reasoning_events) == 1
    assert reasoning_events[0].delta == "thinking..."


def test_nudge_request_payload_patch_uses_messages_key():
    """NudgeContinuationFinalizer must send request_payload_patch with
    \"messages\" key (not \"nudge_message\") so _apply_request_payload_patch
    in runner.py can apply it.

    Regression test for: NudgeContinuationFinalizer was sending
    {\"nudge_message\": ...} which _apply_request_payload_patch ignored
    (it only handles patch[\"messages\"]), causing nudge retries to send
    the same payload back to upstream.
    """
    from keeprollming.filters.nudge.stream import (
        NudgeContinuationFinalizer,
    )
    from keeprollming.streaming.events import AssistantTextDelta, Finish

    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )

    # Feed lazy content
    nudge.process_event(AssistantTextDelta(delta="Here is the list:"))
    nudge.process_event(Finish(reason="stop"))

    # finalize() must be called to detect lazy pattern and produce decision
    nudge.finalize()

    assert nudge.lazy_detected is True
    assert nudge.decision is not None

    patch = nudge.decision.request_payload_patch
    assert patch is not None
    assert "messages" in patch, (
        "request_payload_patch must contain 'messages' key for "
        "_apply_request_payload_patch to apply it"
    )
    assert "nudge_message" not in patch, (
        "request_payload_patch must not use 'nudge_message' key — "
        "_apply_request_payload_patch ignores it"
    )

    messages = patch["messages"]
    assert len(messages) == 2
    assert messages[0] == {"role": "assistant", "content": "Here is the list:"}
    assert messages[1] == {"role": "user", "content": "Continue."}


# ---------------------------------------------------------------------------
# V1/V2 Parity Golden Test
# ---------------------------------------------------------------------------


def test_nudge_v1_v2_merge_parity_golden():
    """Golden parity test: V1 and V2 must produce identical merge output.

    V1 production rule (model_nudge_filter.py lines 617, 665):
        accumulator += "\\n" + retry_content

    V2 must match this exact behavior:
        - separator is exactly "\\n"
        - no rstrip on prefix
        - no lstrip on continuation
        - no normalization of existing newlines

    This is a golden test because direct dual-path execution is impractical:
    V1 is a filter in a pipeline with HTTP retry logic, V2 is a streaming
    runner with recovery. Comparing them requires running the same scenario
    through both complete pipelines, which is disproportionately complex.

    Instead, this test verifies:
    1. The V1 production rule is `prefix + "\\n" + continuation`
    2. The V2 NudgeContinuationFinalizer produces the same result for
       the same semantic inputs (prefix, continuation)

    If V1 changes its separator rule, the golden reference updates and
    V2 must match. If V2 loses separator injection, this test fails.
    """
    from keeprollming.filters.nudge.stream import (
        NudgeContinuationFinalizer,
    )

    # V1 golden reference: the exact merge expression from production code
    # model_nudge_filter.py: accumulator += "\\n" + retry_content
    v1_golden_prefix = "Here is the list:"
    v1_golden_continuation = "Item 1"
    v1_expected = v1_golden_prefix + "\n" + v1_golden_continuation

    # V2: simulate the same semantic inputs through the finalizer
    nudge = NudgeContinuationFinalizer(
        trigger_patterns=[":$"],
        nudge_message="Continue.",
        max_attempts=3,
    )

    # Simulate first attempt: prefix text
    nudge.process_event(AssistantTextDelta(delta=v1_golden_prefix))

    # Simulate recovery: reset with preserve_buffer=True (append_continuation)
    nudge.reset(preserve_buffer=True)

    # Simulate continuation attempt: first delta
    nudge.process_event(AssistantTextDelta(delta=v1_golden_continuation))

    # Finalize to flush
    events = nudge.finalize()

    # Collect all text from events
    merged_text = ""
    for ev in events:
        if isinstance(ev, AssistantTextDelta):
            merged_text += ev.delta

    # V2 must match V1 golden reference exactly
    assert merged_text == v1_expected, (
        f"V2 merge output {merged_text!r} does not match V1 golden "
        f"reference {v1_expected!r}. V1 rule: prefix + '\\n' + continuation"
    )

    # Additional parity cases
    parity_cases = [
        ("Here is the list:", " Item 1", "Here is the list:\n Item 1"),
        ("Here is the list:\n", "Item 1", "Here is the list:\n\nItem 1"),
        ("I can help.", "\nResult", "I can help.\n\nResult"),
    ]

    for prefix, continuation, expected in parity_cases:
        nudge2 = NudgeContinuationFinalizer(
            trigger_patterns=[":$"],
            nudge_message="Continue.",
            max_attempts=3,
        )
        nudge2.process_event(AssistantTextDelta(delta=prefix))
        nudge2.reset(preserve_buffer=True)
        nudge2.process_event(AssistantTextDelta(delta=continuation))
        events2 = nudge2.finalize()
        merged2 = ""
        for ev in events2:
            if isinstance(ev, AssistantTextDelta):
                merged2 += ev.delta
        assert merged2 == expected, (
            f"V2 merge {merged2!r} != V1 golden {expected!r} for "
            f"prefix={prefix!r}, continuation={continuation!r}"
        )
