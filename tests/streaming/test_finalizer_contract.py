"""Tests for the V2 finalizer contract (StreamFinalizer protocol).

Verifies that ``TimestampFinalizer`` and ``ToolCallFinalizer`` conform to
the ``StreamFinalizer`` abstract base class defined in
``keeprollming/streaming/finalizers.py``.
"""

from __future__ import annotations

import pytest

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Done,
    Finish,
    Keepalive,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)
from keeprollming.streaming.finalizers import StreamFinalizer, ToolCallFinalizer
from keeprollming.filters.timestamp.stream import TimestampFinalizer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATE = "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"

_FIXED_DT = "2026-06-29 12:00:00 UTC"


# ---------------------------------------------------------------------------
# ToolCallFinalizer helpers
# ---------------------------------------------------------------------------


def _make_delta(
    index: int = 0,
    id: str | None = None,
    name: str | None = None,
    arguments_delta: str = "",
) -> ToolCallDelta:
    return ToolCallDelta(
        index=index,
        id=id,
        name=name,
        arguments_delta=arguments_delta,
    )


def _make_finish(reason: str = "tool_calls") -> Finish:
    return Finish(reason=reason)


# ---------------------------------------------------------------------------
# Test: TimestampFinalizer implements StreamFinalizer
# ---------------------------------------------------------------------------


def test_timestamp_finalizer_implements_contract():
    """TimestampFinalizer is an instance of StreamFinalizer."""
    finalizer = TimestampFinalizer(template=_DEFAULT_TEMPLATE)
    assert isinstance(finalizer, StreamFinalizer)


# ---------------------------------------------------------------------------
# Test: process_event must be implemented
# ---------------------------------------------------------------------------


def test_process_event_exists():
    """TimestampFinalizer has a process_event method that accepts StreamEvent."""
    finalizer = TimestampFinalizer(template=_DEFAULT_TEMPLATE)
    event = AssistantTextDelta(delta="Hello")
    # process_event is not used by TimestampFinalizer (it uses process_delta),
    # but the contract requires it. It may return an empty list (pass-through).
    result = finalizer.process_event(event)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Test: finalize returns list of StreamEvent
# ---------------------------------------------------------------------------


def test_finalize_returns_list_of_events():
    """finalize() returns a list of StreamEvent objects."""
    finalizer = TimestampFinalizer(template=_DEFAULT_TEMPLATE)
    finalizer.process_delta("Hello")
    result = finalizer.finalize()
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], StreamEvent)


# ---------------------------------------------------------------------------
# Test: process_event buffers short AssistantTextDelta
# ---------------------------------------------------------------------------


def test_process_event_buffers_short_assistant_text():
    """process_event(AssistantTextDelta("Hello")) returns [] for short text.

    finalize() then emits "Hello" + fresh timestamp.
    """
    from datetime import datetime, timezone

    from keeprollming.filters.timestamp.stream import TimestampFinalizer

    fixed_dt = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=lambda: fixed_dt, tail_buffer_size=1024
    )

    # Short content — stays in tail buffer
    result = finalizer.process_event(AssistantTextDelta(delta="Hello"))
    assert result == [], "Short content should stay in tail buffer"

    # finalize emits content + fresh timestamp
    final_list = finalizer.finalize()
    assert len(final_list) == 1
    text = final_list[0].delta
    assert "Hello" in text
    assert "2026-06-29 12:00:00 UTC" in text


# ---------------------------------------------------------------------------
# Test: process_event emits safe prefix for long text
# ---------------------------------------------------------------------------


def test_process_event_emits_safe_prefix_for_long_text():
    """process_event emits safe prefix when tail buffer is exceeded.

    finalize() emits corrected tail.  Concatenation has no duplication/loss.
    """
    from datetime import datetime, timezone

    from keeprollming.filters.timestamp.stream import TimestampFinalizer

    tail_size = 20
    fixed_dt = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=lambda: fixed_dt, tail_buffer_size=tail_size
    )

    long_content = "A" * 50  # 50 chars, well above tail buffer
    result = finalizer.process_event(AssistantTextDelta(delta=long_content))

    # Should emit safe prefix (50 - 20 = 30 chars)
    assert len(result) == 1
    emitted = result[0].delta
    assert len(emitted) == 30
    assert emitted == "A" * 30

    # finalize emits corrected tail
    final_list = finalizer.finalize()
    final_text = final_list[0].delta

    expected_final = "A" * 20 + "\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC"
    assert final_text == expected_final

    # Verify no duplication/loss
    all_parts = [emitted, final_text]
    full_text = "".join(all_parts)
    expected_full = "A" * 50 + "\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC"
    assert full_text == expected_full
    assert full_text.count("A" * 50) == 1


# ---------------------------------------------------------------------------
# Test: process_event passes non-text events through
# ---------------------------------------------------------------------------


def test_process_event_passes_non_text_events_through():
    """process_event(Finish/Done) returns [same event] (pass-through)."""
    from keeprollming.streaming.events import Done, Finish

    finalizer = TimestampFinalizer(template=_DEFAULT_TEMPLATE)

    # Finish
    finish_event = Finish(reason="stop")
    result = finalizer.process_event(finish_event)
    assert len(result) == 1
    assert result[0] is finish_event

    # Done
    done_event = Done()
    result = finalizer.process_event(done_event)
    assert len(result) == 1
    assert result[0] is done_event


# ---------------------------------------------------------------------------
# Test: finalize injects corrected tail event
# ---------------------------------------------------------------------------


def test_finalize_injects_corrected_tail():
    """finalize() injects a corrected AssistantTextDelta into the pipeline."""
    from datetime import datetime, timezone

    from keeprollming.filters.timestamp.stream import TimestampFinalizer

    fixed_dt = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE,
        clock=lambda: fixed_dt,
        tail_buffer_size=1024,
    )

    finalizer.process_delta("Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC")
    events = finalizer.finalize()

    # finalize() returns list[AssistantTextDelta] (StreamFinalizer contract)
    assert len(events) == 1
    assert isinstance(events[0], AssistantTextDelta)
    text = events[0].delta
    assert "Hello" in text
    assert "2020-01-01" not in text
    assert "2026-06-29 12:00:00 UTC" in text


# ---------------------------------------------------------------------------
# Test: abstract base cannot be instantiated directly
# ---------------------------------------------------------------------------


def test_abstract_base_cannot_be_instantiated():
    """StreamFinalizer cannot be instantiated directly (ABC)."""
    with pytest.raises(TypeError):
        StreamFinalizer()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Test: custom finalizer can be written
# ---------------------------------------------------------------------------


def test_custom_finalizer_conforms():
    """A custom StreamFinalizer subclass works with the contract."""

    class CountingFinalizer(StreamFinalizer):
        """Counts AssistantTextDelta events."""

        priority = 10  # Runs before timestamp finalizer

        def __init__(self) -> None:
            self.count = 0

        def process_event(self, event: StreamEvent) -> list[StreamEvent]:
            if isinstance(event, AssistantTextDelta):
                self.count += 1
            return [event]

        def finalize(self) -> list[StreamEvent]:
            return []

    fin = CountingFinalizer()
    fin.process_event(AssistantTextDelta(delta="a"))
    fin.process_event(AssistantTextDelta(delta="b"))
    assert fin.count == 2
    assert fin.finalize() == []


# ---------------------------------------------------------------------------
# ToolCallFinalizer tests
# ---------------------------------------------------------------------------


def test_toolcall_finalizer_implements_contract():
    """ToolCallFinalizer is an instance of StreamFinalizer."""
    finalizer = ToolCallFinalizer()
    assert isinstance(finalizer, StreamFinalizer)


def test_toolcall_finalizer_single_complete_tool_call():
    """A single ToolCallDelta with complete arguments assembles into
    ToolCallComplete on Finish(reason="tool_calls")."""
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    delta = _make_delta(
        index=0,
        id="call_1",
        name="echo",
        arguments_delta='{"message":"hello"}',
    )

    events = finalizer.process_event(delta)
    assert events == [], "ToolCallDelta should be buffered"

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    finishes = [e for e in output if isinstance(e, Finish)]

    assert len(complete_events) == 1
    tc = complete_events[0]
    assert tc.index == 0
    assert tc.id == "call_1"
    assert tc.name == "echo"
    assert tc.arguments_json == '{"message":"hello"}'
    assert tc.arguments_obj == {"message": "hello"}

    assert len(finishes) == 1
    assert finishes[0].reason == "tool_calls"


def test_toolcall_finalizer_arguments_split_across_deltas():
    """Arguments split across multiple ToolCallDelta events are concatenated."""
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d1 = _make_delta(index=0, id="call_1", name="calc", arguments_delta='{"a":')
    assert finalizer.process_event(d1) == []

    d2 = _make_delta(index=0, arguments_delta='"1"}')
    assert finalizer.process_event(d2) == []

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 1
    tc = complete_events[0]
    assert tc.arguments_json == '{"a":"1"}'
    assert tc.arguments_obj == {"a": "1"}
    assert tc.id == "call_1"
    assert tc.name == "calc"


def test_toolcall_finalizer_id_name_separate_from_arguments():
    """id, name, and arguments can arrive in separate ToolCallDelta events."""
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d1 = _make_delta(index=0, id="call_1", name="echo")
    assert finalizer.process_event(d1) == []

    d2 = _make_delta(index=0, arguments_delta='{"text":"hi"}')
    assert finalizer.process_event(d2) == []

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 1
    tc = complete_events[0]
    assert tc.id == "call_1"
    assert tc.name == "echo"
    assert tc.arguments_json == '{"text":"hi"}'
    assert tc.arguments_obj == {"text": "hi"}


def test_toolcall_finalizer_multiple_tool_calls_by_index():
    """Multiple tool calls (different indices) are assembled independently."""
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d0 = _make_delta(index=0, id="call_0", name="echo", arguments_delta='{}')
    assert finalizer.process_event(d0) == []

    d1 = _make_delta(index=1, id="call_1", name="calc", arguments_delta='{}')
    assert finalizer.process_event(d1) == []

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 2

    assert complete_events[0].index == 0
    assert complete_events[0].id == "call_0"
    assert complete_events[1].index == 1
    assert complete_events[1].id == "call_1"


def test_toolcall_finalizer_non_tool_events_pass_through():
    """Non-tool events (text, reasoning, done, keepalive) pass through."""
    finalizer = ToolCallFinalizer()

    text = AssistantTextDelta(delta="Hello")
    assert finalizer.process_event(text) == [text]

    done = Done()
    assert finalizer.process_event(done) == [done]

    keepalive = Keepalive()
    assert finalizer.process_event(keepalive) == [keepalive]

    # Text after tool call buffering
    d = _make_delta(index=0, id="c", name="f", arguments_delta='{}')
    finalizer.process_event(d)  # buffered

    text2 = AssistantTextDelta(delta="World")
    assert finalizer.process_event(text2) == [text2]


def test_toolcall_finalizer_finish_tool_calls_flushes_before_finish():
    """Finish(reason='tool_calls') emits ToolCallComplete before Finish."""
    finalizer = ToolCallFinalizer()

    d = _make_delta(index=0, id="c", name="f", arguments_delta='{}')
    finalizer.process_event(d)

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    tc_indices = [i for i, e in enumerate(output) if isinstance(e, ToolCallComplete)]
    finish_idx = next(i for i, e in enumerate(output) if isinstance(e, Finish))

    assert len(tc_indices) == 1
    assert tc_indices[0] < finish_idx, (
        "ToolCallComplete must appear before Finish"
    )


def test_toolcall_finalizer_done_passthrough():
    """Done passes through unchanged."""
    finalizer = ToolCallFinalizer()
    done = Done()
    assert finalizer.process_event(done) == [done]


def test_toolcall_finalizer_finalize_flushes_pending_valid_tool_call():
    """finalize() flushes pending valid tool calls if no Finish was seen."""
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d = _make_delta(index=0, id="c", name="f", arguments_delta='{"ok":true}')
    finalizer.process_event(d)

    result = finalizer.finalize()

    complete_events = [e for e in result if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 1
    tc = complete_events[0]
    assert tc.arguments_json == '{"ok":true}'
    assert tc.arguments_obj == {"ok": True}


def test_toolcall_finalizer_no_duplicate_on_finalize_after_finish():
    """finalize() returns [] after Finish already flushed ToolCallComplete.

    Note: finalize() sets _flushed=True, so when process_event is called
    after finalize(), it still emits ToolCallComplete (because _flushed was
    set by finalize, not by process_event).  But if process_event flushes
    first, finalize() returns [].
    """
    finalizer = ToolCallFinalizer()

    d = _make_delta(index=0, id="c", name="f", arguments_delta='{}')
    finalizer.process_event(d)

    finish = _make_finish("tool_calls")
    finalizer.process_event(finish)  # flushed ToolCallComplete

    # finalize() after process_event flush: returns [] (no duplicate)
    result = finalizer.finalize()
    complete_events = [e for e in result if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 0, "finalize() after flush should return []"

    # Verify no duplicate when finalize called before process_event
    finalizer2 = ToolCallFinalizer()
    d2 = _make_delta(index=0, id="c2", name="f2", arguments_delta='{}')
    finalizer2.process_event(d2)
    complete_from_finalize = finalizer2.finalize()  # first finalize
    assert len([e for e in complete_from_finalize if isinstance(e, ToolCallComplete)]) == 1
    finish2 = _make_finish("tool_calls")
    output = finalizer2.process_event(finish2)
    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    # No duplicate: finalize already flushed, so process_event returns only Finish
    assert len(complete_events) == 0


def test_toolcall_finalizer_invalid_json_no_crash():
    """Invalid/incomplete JSON arguments are handled gracefully."""
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d = _make_delta(index=0, id="c", name="f", arguments_delta='{"unclosed":')
    finalizer.process_event(d)

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    # Invalid JSON should NOT be emitted as ToolCallComplete
    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 0

    # But Finish should still be present
    finishes = [e for e in output if isinstance(e, Finish)]
    assert len(finishes) == 1

    # With flush_valid_only=False, invalid JSON is emitted with arguments_obj=None
    finalizer2 = ToolCallFinalizer(flush_valid_only=False)
    d2 = _make_delta(index=0, id="c2", name="f2", arguments_delta='{"unclosed":')
    finalizer2.process_event(d2)

    finish2 = _make_finish("tool_calls")
    output2 = finalizer2.process_event(finish2)

    complete_events2 = [e for e in output2 if isinstance(e, ToolCallComplete)]
    assert len(complete_events2) == 1
    tc = complete_events2[0]
    assert tc.arguments_json == '{"unclosed":'
    assert tc.arguments_obj is None


def test_toolcall_finalizer_non_tool_finish_flushes_valid_tool_calls():
    """Finish(reason='stop') flushes valid tool calls and upgrades to
    Finish(reason='tool_calls') for I9 compliance."""
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d = _make_delta(index=0, id="c", name="f", arguments_delta='{"ok":true}')
    finalizer.process_event(d)

    finish = _make_finish("stop")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    finishes = [e for e in output if isinstance(e, Finish)]

    assert len(complete_events) == 1
    assert complete_events[0].arguments_obj == {"ok": True}
    assert len(finishes) == 1
    # I9: when ToolCallComplete is emitted, Finish is upgraded to tool_calls
    assert finishes[0].reason == "tool_calls"


def test_toolcall_finalizer_finalize_second_call_raises():
    """Second finalize() raises RuntimeError."""
    finalizer = ToolCallFinalizer()
    finalizer.finalize()
    with pytest.raises(RuntimeError, match="already called"):
        finalizer.finalize()


def test_toolcall_finalizer_invalid_only_pending_no_zero_finish():
    """Invalid-only pending tool call + Finish(reason='stop') does NOT emit
    Finish(reason='tool_calls') with zero tool calls.

    I9: when invalid JSON is dropped (flush_valid_only=True), no
    ToolCallComplete is emitted, so Finish keeps its original reason.
    """
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d = _make_delta(index=0, id="c", name="f", arguments_delta='{"unclosed":')
    finalizer.process_event(d)

    finish = _make_finish("stop")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    finishes = [e for e in output if isinstance(e, Finish)]

    assert len(complete_events) == 0, "No valid ToolCallComplete should be emitted"
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"


def test_toolcall_finalizer_valid_flush_then_finish():
    """Valid ToolCallComplete + Finish(reason='tool_calls') passes strict validator."""
    from tests.helpers.stream_client import (
        TestToolCallComplete,
        TestFinish,
        TestDone,
        assert_stream_protocol_valid,
    )

    finalizer = ToolCallFinalizer(flush_valid_only=True)

    d = _make_delta(index=0, id="call_1", name="echo", arguments_delta='{"msg":"hi"}')
    finalizer.process_event(d)

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    # Build a sequence: ToolCallComplete + Finish + Done
    from keeprollming.streaming.events import Done
    events = output + [Done()]

    # Serialize and parse through stream_client
    from keeprollming.streaming.serializer import OpenAISSESerializer
    from tests.helpers.stream_client import parse_sse_events

    ser = OpenAISSESerializer()
    frames = ser.serialize_events(events)
    parsed = parse_sse_events(frames)

    assert_stream_protocol_valid(parsed, profile="strict")


def test_toolcall_finalizer_non_tool_finish_with_tc_fails_strict():
    """ToolCallFinalizer with pending valid tool call + Finish(reason='stop')
    outputs Finish(reason='tool_calls'), which passes strict.

    But if we manually construct ToolCallComplete + Finish(reason='stop'),
    strict validator rejects it.
    """
    from tests.helpers.stream_client import (
        TestToolCallComplete,
        TestFinish,
        TestDone,
        assert_stream_protocol_valid,
    )

    # Direct sequence: ToolCallComplete + Finish(reason='stop') + Done
    from keeprollming.streaming.events import ToolCallComplete, Done
    events = [
        ToolCallComplete(
            index=0,
            id="call_1",
            name="echo",
            arguments_json='{"msg":"hi"}',
            arguments_obj={"msg": "hi"},
        ),
        Finish(reason="stop"),
        Done(),
    ]

    from keeprollming.streaming.serializer import OpenAISSESerializer
    from tests.helpers.stream_client import parse_sse_events

    ser = OpenAISSESerializer()
    frames = ser.serialize_events(events)
    parsed = parse_sse_events(frames)

    # I9: ToolCallComplete present but finish_reason != tool_calls
    with pytest.raises(AssertionError, match="I9"):
        assert_stream_protocol_valid(parsed, profile="strict")


# ---------------------------------------------------------------------------
# C1.5: ToolCallFinalizer reset() support
# ---------------------------------------------------------------------------


def test_toolcall_finalizer_reset_clears_partial_buffer():
    """reset() clears partial tool-call assembly buffer.

    Scenario:
    1. Process partial ToolCallDelta for call A
    2. Call reset()
    3. Process ToolCallDelta for call B
    4. finalize()
    5. Assert only B is completed, A is absent
    """
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    # Process partial ToolCallDelta for call A (incomplete JSON)
    delta_a = _make_delta(
        index=0,
        id="call_A",
        name="func_a",
        arguments_delta='{"key":',  # incomplete JSON
    )
    finalizer.process_event(delta_a)
    assert len(finalizer._buffers) == 1
    assert "call_A" in str(finalizer._buffers)

    # Call reset()
    finalizer.reset()

    # Verify buffer is cleared
    assert len(finalizer._buffers) == 0
    assert finalizer._flushed is False
    assert finalizer._finalized is False

    # Process ToolCallDelta for call B (complete JSON)
    delta_b = _make_delta(
        index=1,
        id="call_B",
        name="func_b",
        arguments_delta='{"msg":"hello"}',
    )
    finalizer.process_event(delta_b)
    assert len(finalizer._buffers) == 1

    # finalize() should only emit call B
    result = finalizer.finalize()
    complete_events = [e for e in result if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 1
    assert complete_events[0].id == "call_B"
    assert complete_events[0].name == "func_b"
    assert complete_events[0].arguments_obj == {"msg": "hello"}


def test_toolcall_finalizer_reset_idempotent():
    """reset() is idempotent and safe to call before any events.

    Scenario:
    1. Call reset() before any events
    2. Call reset() again
    3. No exception
    4. finalize() returns no stale output
    """
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    # Call reset() before any events
    finalizer.reset()
    assert len(finalizer._buffers) == 0

    # Call reset() again
    finalizer.reset()
    assert len(finalizer._buffers) == 0

    # finalize() should return empty list (no stale output)
    result = finalizer.finalize()
    complete_events = [e for e in result if isinstance(e, ToolCallComplete)]
    assert len(complete_events) == 0


def test_toolcall_finalizer_normal_assembly_unchanged():
    """Normal tool-call assembly behavior is unchanged after reset() addition.

    Scenario:
    1. Process ToolCallDelta with complete JSON
    2. Process Finish(reason='tool_calls')
    3. Assert ToolCallComplete is emitted with correct data
    """
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    delta = _make_delta(
        index=0,
        id="call_1",
        name="echo",
        arguments_delta='{"message":"hello"}',
    )
    finalizer.process_event(delta)

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    finishes = [e for e in output if isinstance(e, Finish)]

    assert len(complete_events) == 1
    tc = complete_events[0]
    assert tc.index == 0
    assert tc.id == "call_1"
    assert tc.name == "echo"
    assert tc.arguments_json == '{"message":"hello"}'
    assert tc.arguments_obj == {"message": "hello"}

    assert len(finishes) == 1
    assert finishes[0].reason == "tool_calls"


def test_toolcall_finalizer_i9_still_supported():
    """I9 invariant is preserved: ToolCallComplete + Finish(reason='tool_calls').

    Scenario:
    1. Process ToolCallDelta with valid JSON
    2. Process Finish(reason='tool_calls')
    3. Assert Finish.reason is "tool_calls" (I9 compliant)
    """
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    delta = _make_delta(
        index=0,
        id="call_1",
        name="echo",
        arguments_delta='{"ok":true}',
    )
    finalizer.process_event(delta)

    finish = _make_finish("tool_calls")
    output = finalizer.process_event(finish)

    complete_events = [e for e in output if isinstance(e, ToolCallComplete)]
    finishes = [e for e in output if isinstance(e, Finish)]

    # I9: if ToolCallComplete is emitted, Finish.reason must be "tool_calls"
    assert len(complete_events) == 1
    assert len(finishes) == 1
    assert finishes[0].reason == "tool_calls"


def test_toolcall_finalizer_reset_preserve_buffer():
    """reset(preserve_buffer=True) preserves the assembly buffer.

    Scenario:
    1. Process ToolCallDelta
    2. Call reset(preserve_buffer=True)
    3. Assert buffer is preserved
    """
    finalizer = ToolCallFinalizer(flush_valid_only=True)

    delta = _make_delta(
        index=0,
        id="call_1",
        name="echo",
        arguments_delta='{"msg":"hello"}',
    )
    finalizer.process_event(delta)
    assert len(finalizer._buffers) == 1

    # Call reset with preserve_buffer=True
    finalizer.reset(preserve_buffer=True)

    # Buffer should be preserved
    assert len(finalizer._buffers) == 1
    assert "call_1" in str(finalizer._buffers)

    # But _flushed and _finalized should be reset
    assert finalizer._flushed is False
    assert finalizer._finalized is False
