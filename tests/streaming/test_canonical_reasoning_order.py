"""Streaming reasoning-order tests for the V2 pipeline.

Tests the KRM canonical channel ordering contract (Option A — Grouped):
- R1: Semantic channel grouping (reasoning and assistant text in separate channels)
- R2: Preserve upstream frame order; when channels share a frame, reasoning
  precedes assistant content
- R3: Intra-channel order preservation
- R4: Passive finalizer canonical-order stability
- R5: Recovery preservation by strategy

See _docs/streaming-v2-reasoning-order-contract.md for the full contract.
"""

import json

import pytest

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Done,
    Finish,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallDelta,
)
from keeprollming.filters.nudge.stream import NudgeContinuationFinalizer
from keeprollming.streaming.parser import StreamParser
from keeprollming.streaming.serializer import OpenAISSESerializer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_chunk(payload: dict) -> bytes:
    """Build a single SSE chunk from a payload dict."""
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def _parse_and_collect(
    chunks: list[bytes],
    finalizers: list | None = None,
) -> list[StreamEvent]:
    """Parse upstream SSE chunks into events, run through finalizers.

    Returns the list of events produced by the parser (and finalizers if provided).
    Simulates the runner's event processing: parse → pass through finalizers → finalize.
    """
    parser = StreamParser()
    events = parser.parse_sync(chunks)

    if finalizers:
        from keeprollming.streaming.runner import _pass_through_finalizers_with_buffer_tracking

        # Pass events through finalizers
        all_events = []
        for event in events:
            produced, _ = _pass_through_finalizers_with_buffer_tracking(
                [event], finalizers
            )
            all_events.extend(produced)

        # Run finalize on all finalizers
        # finalize() output is emitted after pass-through events
        # But for testing, we want: finalize output first, then Finish/Done
        finalize_output = []
        for fin in finalizers:
            if hasattr(fin, "finalize"):
                try:
                    finalize_events = fin.finalize()
                    finalize_output.extend(finalize_events)
                except RuntimeError:
                    pass

        # Combine: finalize output first, then pass-through events
        # This matches the runner's behavior where finalize() output
        # is emitted before Finish/Done
        all_events = finalize_output + all_events

        return all_events

    return events


def _event_types(events: list[StreamEvent]) -> list[str]:
    """Extract event type names from a list of events."""
    return [type(e).__name__ for e in events]


def _reasoning_events(events: list[StreamEvent]) -> list[str]:
    """Extract reasoning text from ReasoningTextDelta events."""
    return [e.delta for e in events if isinstance(e, ReasoningTextDelta)]


def _assistant_events(events: list[StreamEvent]) -> list[str]:
    """Extract assistant text from AssistantTextDelta events."""
    return [e.delta for e in events if isinstance(e, AssistantTextDelta)]


# ---------------------------------------------------------------------------
# R1/R2/R3: Parser streaming order
# ---------------------------------------------------------------------------


class TestParserStreamingOrder:
    """Test parser delivery order without terminal response buffering."""

    def test_reasoning_only(self):
        """Reasoning-only stream: Reasoning → Finish → Done."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _event_types(events) == [
            "ReasoningTextDelta",
            "Finish",
            "Done",
        ]
        assert _reasoning_events(events) == ["R1"]

    def test_content_only(self):
        """Content-only stream: Assistant → Finish → Done."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _event_types(events) == [
            "AssistantTextDelta",
            "Finish",
            "Done",
        ]
        assert _assistant_events(events) == ["C1"]

    def test_reasoning_then_content(self):
        """Reasoning before content (separate frames): Reasoning → Assistant → Finish → Done."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _event_types(events) == [
            "ReasoningTextDelta",
            "AssistantTextDelta",
            "Finish",
            "Done",
        ]
        assert _reasoning_events(events) == ["R1"]
        assert _assistant_events(events) == ["C1"]

    def test_content_then_reasoning_preserves_upstream_frame_order(self):
        """Separate upstream frames remain observable in their arrival order."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _event_types(events) == [
            "AssistantTextDelta",
            "ReasoningTextDelta",
            "Finish",
            "Done",
        ]
        assert _reasoning_events(events) == ["R1"]
        assert _assistant_events(events) == ["C1"]

    def test_interleaved_r1_c1_r2_c2_is_not_collapsed(self):
        """Each delta is delivered immediately instead of waiting for Finish."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R2"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C2"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _event_types(events) == [
            "ReasoningTextDelta",
            "AssistantTextDelta",
            "ReasoningTextDelta",
            "AssistantTextDelta",
            "Finish",
            "Done",
        ]
        assert _reasoning_events(events) == ["R1", "R2"]
        assert _assistant_events(events) == ["C1", "C2"]

    def test_same_frame_reasoning_and_content(self):
        """Same-frame reasoning + content: Reasoning → Assistant → Finish → Done."""
        chunks = [
            _sse_chunk({
                "choices": [{
                    "delta": {
                        "role": "assistant",
                        "reasoning_content": "R1",
                        "content": "C1",
                        "finish_reason": "stop",
                    }
                }]
            }),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _event_types(events) == [
            "ReasoningTextDelta",
            "AssistantTextDelta",
            "Finish",
            "Done",
        ]
        assert _reasoning_events(events) == ["R1"]
        assert _assistant_events(events) == ["C1"]

    def test_no_reasoning_no_content(self):
        """No reasoning, no content: Finish → Done."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _event_types(events) == [
            "Finish",
            "Done",
        ]

    def test_intra_channel_order_preserved(self):
        """Intra-channel order preserved: R1 before R2, C1 before C2."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R2"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R3"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C2"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C3"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)
        assert _reasoning_events(events) == ["R1", "R2", "R3"]
        assert _assistant_events(events) == ["C1", "C2", "C3"]


# ---------------------------------------------------------------------------
# R4: Passive finalizer canonical-order stability
# ---------------------------------------------------------------------------


class TestPassiveFinalizerStability:
    """Test passive finalizer canonical-order stability (R4)."""

    def test_nudge_no_trigger_preserves_canonical_order(self):
        """Nudge enabled but not triggered: same canonical order as Nudge disabled."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]

        # With Nudge disabled (no finalizers)
        events_no_nudge = _parse_and_collect(chunks)
        types_no_nudge = _event_types(events_no_nudge)

        # With Nudge enabled but not triggered (no lazy pattern)
        nudge = NudgeContinuationFinalizer()
        events_with_nudge = _parse_and_collect(chunks, finalizers=[nudge])
        types_with_nudge = _event_types(events_with_nudge)

        # Both should have the same canonical order (Reasoning before Assistant)
        # Note: Nudge buffers events and re-emits them at finalize()
        # The order should be: ReasoningTextDelta, AssistantTextDelta, Finish, Done
        assert types_with_nudge == [
            "ReasoningTextDelta",
            "AssistantTextDelta",
            "Finish",
            "Done",
        ]

    def test_nudge_no_trigger_reasoning_before_text(self):
        """Nudge enabled but not triggered: reasoning before text."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        nudge = NudgeContinuationFinalizer()
        events = _parse_and_collect(chunks, finalizers=[nudge])

        reasoning = _reasoning_events(events)
        assistant = _assistant_events(events)

        assert reasoning == ["R1"]
        assert assistant == ["C1"]

        # Reasoning should come before assistant in the event list
        reasoning_idx = next(
            i for i, e in enumerate(events) if isinstance(e, ReasoningTextDelta)
        )
        assistant_idx = next(
            i for i, e in enumerate(events) if isinstance(e, AssistantTextDelta)
        )
        assert reasoning_idx < assistant_idx


# ---------------------------------------------------------------------------
# R5: Recovery preservation by strategy
# ---------------------------------------------------------------------------


class TestRecoveryPreservation:
    """Test recovery preservation by strategy (R5)."""

    def test_append_continuation_preserves_reasoning(self):
        """append_continuation: reasoning from all attempts preserved."""
        # Simulate two attempts via Nudge
        # Lazy pattern ":$" matches content ending with ":"
        nudge = NudgeContinuationFinalizer(trigger_patterns=[":$"])

        # Attempt 1: reasoning + lazy text (ends with ":")
        nudge.process_event(ReasoningTextDelta(delta="R1"))
        nudge.process_event(AssistantTextDelta(delta="Hello:"))

        # Finalize triggers recovery
        events1 = nudge.finalize()
        assert nudge.decision is not None
        assert nudge.decision.kind == "append_continuation"

        # Reset with preserve_buffer=True (append_continuation)
        nudge.reset(preserve_buffer=True)

        # Attempt 2: continuation text (no reasoning)
        nudge.process_event(AssistantTextDelta(delta=" — continuation"))

        # Finalize (no lazy detected in continuation)
        events2 = nudge.finalize()

        # Reasoning from attempt 1 preserved
        reasoning = _reasoning_events(events2)
        assistant = _assistant_events(events2)

        # Reasoning preserved, text merged with newline separator
        assert reasoning == ["R1"]
        assert assistant == ["Hello:\n — continuation"]

    def test_multi_nudge_preserves_reasoning_in_order(self):
        """Multi-Nudge: reasoning from all attempts preserved in attempt order."""
        # Lazy pattern ":$" matches content ending with ":"
        nudge = NudgeContinuationFinalizer(trigger_patterns=[":$"])

        # Attempt 1
        nudge.process_event(ReasoningTextDelta(delta="R1"))
        nudge.process_event(AssistantTextDelta(delta="P:"))
        nudge.finalize()
        nudge.reset(preserve_buffer=True)

        # Attempt 2
        nudge.process_event(ReasoningTextDelta(delta="R2"))
        nudge.process_event(AssistantTextDelta(delta="M:"))
        nudge.finalize()
        nudge.reset(preserve_buffer=True)

        # Attempt 3
        nudge.process_event(AssistantTextDelta(delta="C"))
        events = nudge.finalize()

        reasoning = _reasoning_events(events)
        assistant = _assistant_events(events)

        # Reasoning from all attempts preserved in order
        assert reasoning == ["R1R2"]
        # Text merged with newline separators
        assert assistant == ["P:\nM:\nC"]

    def test_replace_recovery_discards_reasoning(self):
        """replace recovery: reasoning from rejected attempt discarded."""
        nudge = NudgeContinuationFinalizer()

        # Buffer some reasoning and text
        nudge.process_event(ReasoningTextDelta(delta="R1"))
        nudge.process_event(AssistantTextDelta(delta="Rejected"))

        # Reset with preserve_buffer=False (replace strategy)
        nudge.reset(preserve_buffer=False)

        # New attempt
        nudge.process_event(ReasoningTextDelta(delta="R2"))
        nudge.process_event(AssistantTextDelta(delta="Accepted"))

        events = nudge.finalize()

        reasoning = _reasoning_events(events)
        assistant = _assistant_events(events)

        assert reasoning == ["R2"]
        assert assistant == ["Accepted"]


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


class TestInvariants:
    """Test protocol invariants (I4, I5, I1, I2/I3)."""

    def test_no_reasoning_after_finish(self):
        """I5: No ReasoningTextDelta after Finish."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)

        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, Finish)
        )
        for i, e in enumerate(events):
            if i > finish_idx:
                assert not isinstance(e, ReasoningTextDelta), (
                    f"ReasoningTextDelta found after Finish at index {i}"
                )

    def test_no_assistant_after_finish(self):
        """I4: No AssistantTextDelta after Finish."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)

        finish_idx = next(
            i for i, e in enumerate(events) if isinstance(e, Finish)
        )
        for i, e in enumerate(events):
            if i > finish_idx:
                assert not isinstance(e, AssistantTextDelta), (
                    f"AssistantTextDelta found after Finish at index {i}"
                )

    def test_finish_exactly_once(self):
        """I1: Exactly one Finish per stream."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)

        finish_count = sum(1 for e in events if isinstance(e, Finish))
        assert finish_count == 1

    def test_done_exactly_once_and_last(self):
        """I2/I3: Exactly one Done, and it is the last event."""
        chunks = [
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "reasoning_content": "R1"}}]}),
            _sse_chunk({"choices": [{"delta": {"role": "assistant", "content": "C1"}}]}),
            _sse_chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n\n",
        ]
        events = _parse_and_collect(chunks)

        done_count = sum(1 for e in events if isinstance(e, Done))
        assert done_count == 1
        assert isinstance(events[-1], Done)
