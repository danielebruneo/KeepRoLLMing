"""Unit tests for OpenAISSESerializer — V2 streaming serializer.

Verifies that canonical ``StreamEvent`` objects serialize to downstream SSE
that is parseable by ``tests/helpers/stream_client.py``.
"""

from __future__ import annotations

import pytest

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Done,
    Error,
    Finish,
    Keepalive,
    ReasoningTextDelta,
    ToolCallComplete,
    ToolCallDelta,
)
from keeprollming.streaming.serializer import (
    OpenAISSESerializer,
    serialize_event,
    serialize_events,
)
from tests.helpers.stream_client import (
    TestAssistantTextDelta,
    TestDone,
    TestFinish,
    TestKeepalive,
    TestStreamEvent,
    parse_sse_events,
    collect_assistant_text,
    assert_stream_protocol_valid,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SER = OpenAISSESerializer()


# ---------------------------------------------------------------------------
# test_serialize_assistant_text_delta_parseable
# ---------------------------------------------------------------------------


def test_serialize_assistant_text_delta_parseable():
    """AssistantTextDelta("Hello") → parse back → collected text == "Hello"."""
    event = AssistantTextDelta(delta="Hello")
    frame = _SER.serialize_event(event)
    events = parse_sse_events([frame])

    text = collect_assistant_text(events)
    assert text == "Hello"


def test_serializer_keeps_envelope_stable_and_emits_role_once():
    """A client sees one stable OpenAI response envelope, not empty ids."""
    import json

    serializer = OpenAISSESerializer()
    metadata = {"model": "test-model", "created": 123}
    frames = serializer.serialize_events([
        ReasoningTextDelta(delta="think", event_id="chatcmpl-response_1", metadata=metadata),
        ToolCallComplete(
            index=0,
            id="call_1",
            name="echo",
            arguments_json='{"text":"hello"}',
            event_id="chatcmpl-response_1",
            metadata=metadata,
        ),
        Finish(reason="tool_calls", event_id="chatcmpl-response_1", metadata=metadata),
    ])
    payloads = [
        json.loads(frame.decode().removeprefix("data: ").strip())
        for frame in frames
    ]

    assert {payload["id"] for payload in payloads} == {"chatcmpl-response_1"}
    assert {payload["model"] for payload in payloads} == {"test-model"}
    assert {payload["created"] for payload in payloads} == {123}
    assert payloads[0]["choices"][0]["delta"]["role"] == "assistant"
    assert "role" not in payloads[1]["choices"][0]["delta"]


# ---------------------------------------------------------------------------
# test_serialize_unicode_and_quotes
# ---------------------------------------------------------------------------


def test_serialize_unicode_and_quotes():
    """Content with unicode, quotes, newline, backslash round-trips exactly."""
    content = 'Hello "world"\n日本語 \\escaped\\'
    event = AssistantTextDelta(delta=content)
    frame = _SER.serialize_event(event)
    events = parse_sse_events([frame])

    text = collect_assistant_text(events)
    assert text == content


# ---------------------------------------------------------------------------
# test_serialize_finish
# ---------------------------------------------------------------------------


def test_serialize_finish():
    """Finish(reason='stop') → parse → one TestFinish with reason 'stop'."""
    event = Finish(reason="stop")
    frame = _SER.serialize_event(event)
    events = parse_sse_events([frame])

    finish_events = [e for e in events if isinstance(e, TestFinish)]
    assert len(finish_events) == 1
    assert finish_events[0].reason == "stop"


def test_finish_frame_has_no_content_in_delta():
    """Finish serializes with delta == {} (no content field)."""
    import json

    event = Finish(reason="stop")
    frame = _SER.serialize_event(event)

    # Parse the SSE frame
    raw = frame.decode("utf-8")
    data_line = raw.split("\n\n")[0].removeprefix("data: ")
    payload = json.loads(data_line)

    delta = payload["choices"][0]["delta"]
    assert "content" not in delta, (
        "Finish frame must not contain 'content' in delta"
    )
    assert delta == {}, f"Expected empty delta, got {delta}"


# ---------------------------------------------------------------------------
# test_serialize_done
# ---------------------------------------------------------------------------


def test_serialize_done():
    """Done() → parse → one TestDone."""
    event = Done()
    frame = _SER.serialize_event(event)
    events = parse_sse_events([frame])

    done_events = [e for e in events if isinstance(e, TestDone)]
    assert len(done_events) == 1


# ---------------------------------------------------------------------------
# test_serialize_sequence_text_finish_done
# ---------------------------------------------------------------------------


def test_serialize_sequence_text_finish_done():
    """[AssistantTextDelta, Finish, Done] → strict protocol valid."""
    events_in = [
        AssistantTextDelta(delta="Hello"),
        Finish(reason="stop"),
        Done(),
    ]
    frames = _SER.serialize_events(events_in)
    parsed = parse_sse_events(frames)  # serialize_events returns a list — OK
    assert_stream_protocol_valid(parsed, profile="strict")


# ---------------------------------------------------------------------------
# test_no_content_after_finish_sequence_validation
# ---------------------------------------------------------------------------


def test_no_content_after_finish_sequence_validation():
    """Text after Finish is NOT hidden by serializer — validator rejects it."""
    events_in = [
        AssistantTextDelta(delta="Before"),
        Finish(reason="stop"),
        AssistantTextDelta(delta="After"),  # invalid: after finish
        Done(),
    ]
    frames = _SER.serialize_events(events_in)
    parsed = parse_sse_events(frames)  # list of frames — OK

    # Parsing succeeds (serializer doesn't reorder), but validator rejects.
    with pytest.raises(AssertionError, match="I4"):
        assert_stream_protocol_valid(parsed, profile="strict")


# ---------------------------------------------------------------------------
# test_timestamp_finalizer_output_serializes_valid_stream
# ---------------------------------------------------------------------------


def test_timestamp_finalizer_output_serializes_valid_stream():
    """TimestampFinalizer output → serialize → strict protocol valid + one footer."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from keeprollming.filters.timestamp.stream import TimestampFinalizer

    template = "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"
    fixed_dt = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)

    finalizer = TimestampFinalizer(
        template=template, clock=lambda: fixed_dt, tail_buffer_size=1024
    )

    # Feed a response with a stale footer
    finalizer.process_delta(
        "Hello world\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC"
    )

    # Collect safe emitted deltas (may be empty for short content)
    safe_deltas = finalizer.process_delta("")

    # Finalize — returns list[AssistantTextDelta]
    final_list = finalizer.finalize()
    final_delta = final_list[0]

    # Build the event sequence
    events_in: list = list(safe_deltas) + [final_delta, Finish(reason="stop"), Done()]

    frames = _SER.serialize_events(events_in)
    parsed = parse_sse_events(frames)

    # Full protocol valid
    assert_stream_protocol_valid(parsed, profile="strict")

    # Collect assistant text
    text = collect_assistant_text(parsed)

    # Verify: exactly one fresh timestamp footer
    assert "2026-06-29 12:00:00 UTC" in text
    assert "Timestamp: 2020-01-01" not in text

    # Count timestamp footers
    import re
    footer_count = len(re.findall(r"\n*---\n\[?Timestamp: .+", text))
    assert footer_count == 1, f"Expected exactly 1 timestamp footer, got {footer_count}"


# ---------------------------------------------------------------------------
# test_serialize_reasoning_text_delta
# ---------------------------------------------------------------------------


def test_serialize_reasoning_text_delta():
    """ReasoningTextDelta → SSE with reasoning_content."""
    event = ReasoningTextDelta(delta="Let me think...")
    frame = _SER.serialize_event(event)
    events = parse_sse_events([frame])

    reasoning_events = [e for e in events if hasattr(e, "delta") and not isinstance(e, TestAssistantTextDelta)]
    # The stream client stores reasoning as TestReasoningTextDelta
    from tests.helpers.stream_client import TestReasoningTextDelta
    reasoning_events = [e for e in events if isinstance(e, TestReasoningTextDelta)]
    assert len(reasoning_events) == 1
    assert reasoning_events[0].delta == "Let me think..."


# ---------------------------------------------------------------------------
# test_serialize_tool_call_delta
# ---------------------------------------------------------------------------


def test_serialize_tool_call_delta():
    """ToolCallDelta → SSE with tool_calls array."""
    event = ToolCallDelta(index=0, id="call_1", name="echo", arguments_delta='{"msg":"hi"}')
    frame = _SER.serialize_event(event)
    events = parse_sse_events([frame])

    from tests.helpers.stream_client import TestToolCallDelta
    tc_deltas = [e for e in events if isinstance(e, TestToolCallDelta)]
    assert len(tc_deltas) == 1
    assert tc_deltas[0].index == 0
    assert tc_deltas[0].name == "echo"


# ---------------------------------------------------------------------------
# test_serialize_tool_call_complete
# ---------------------------------------------------------------------------


def test_serialize_tool_call_complete():
    """ToolCallComplete + Finish → SSE with TestToolCallComplete emitted."""
    events_in = [
        ToolCallComplete(
            index=0,
            id="call_1",
            name="echo",
            arguments_json='{"msg":"hello"}',
        ),
        Finish(reason="stop"),
        Done(),
    ]
    frames = _SER.serialize_events(events_in)
    parsed = parse_sse_events(frames)

    from tests.helpers.stream_client import TestToolCallComplete
    tc_complete = [e for e in parsed if isinstance(e, TestToolCallComplete)]
    assert len(tc_complete) == 1
    assert tc_complete[0].name == "echo"
    assert tc_complete[0].arguments_json == '{"msg":"hello"}'


# ---------------------------------------------------------------------------
# test_serialize_keepalive
# ---------------------------------------------------------------------------


def test_serialize_keepalive():
    """Keepalive → SSE comment."""
    event = Keepalive()
    frames = _SER.serialize_event(event)

    # Should be a comment line: ": keepalive\\n\\n"
    assert frames == b": keepalive\n\n"


# ---------------------------------------------------------------------------
# test_serialize_error
# ---------------------------------------------------------------------------


def test_serialize_error():
    """Error → SSE error data frame with message and code."""
    event = Error(code="internal_error", message="Something went wrong")
    frame = _SER.serialize_event(event)

    # The stream client's _parse_json_chunk expects "choices" key.
    # Error frames use {"error": {...}} which has no "choices", so they
    # are silently skipped. Verify the raw frame contains the error info.
    import json
    data = json.loads(frame.decode("utf-8").split("\n\n")[0].removeprefix("data: "))
    assert data["error"]["message"] == "Something went wrong"
    assert data["error"]["type"] == "internal_error"


# ---------------------------------------------------------------------------
# test_unhandled_event_type_raises
# ---------------------------------------------------------------------------


def test_unhandled_event_type_raises():
    """A subclass of StreamEvent not in the dispatch table raises ValueError."""
    from keeprollming.streaming.events import StreamEvent

    class UnknownEvent(StreamEvent):
        pass

    event = UnknownEvent()
    with pytest.raises(ValueError, match="Unhandled StreamEvent type: UnknownEvent"):
        _SER.serialize_event(event)


# ---------------------------------------------------------------------------
# test_frame_ends_with_double_newline
# ---------------------------------------------------------------------------


def test_frame_ends_with_double_newline():
    """Every serialized frame ends with \\n\\n (parseable by stream_client)."""
    frames = _SER.serialize_events([
        AssistantTextDelta(delta="test"),
        Finish(reason="stop"),
        Done(),
    ])
    for frame in frames:
        assert frame.endswith(b"\n\n"), f"Frame does not end with \\n\\n: {frame!r}"


# ---------------------------------------------------------------------------
# test_serialize_sequence_with_unicode_and_finish
# ---------------------------------------------------------------------------


def test_serialize_sequence_with_unicode_and_finish():
    """Unicode content + Finish + Done → strict protocol valid."""
    content = "Café résumé naïve 日本語 🌍"
    events_in = [
        AssistantTextDelta(delta=content),
        Finish(reason="stop"),
        Done(),
    ]
    frames = _SER.serialize_events(events_in)
    parsed = parse_sse_events(frames)
    assert_stream_protocol_valid(parsed, profile="strict")
    assert collect_assistant_text(parsed) == content


# ---------------------------------------------------------------------------
# test_no_extra_done_in_sequence
# ---------------------------------------------------------------------------


def test_no_extra_done_in_sequence():
    """Only Done() emits [DONE], not Finish or other events."""
    events_in = [
        AssistantTextDelta(delta="Hello"),
        Finish(reason="stop"),
    ]
    frames = _SER.serialize_events(events_in)
    parsed = parse_sse_events(frames)

    done_events = [e for e in parsed if isinstance(e, TestDone)]
    assert len(done_events) == 0, "Finish should not emit [DONE]"


def test_tool_call_complete_with_stop_finish_not_strict_valid():
    """ToolCallComplete + Finish(reason='stop') fails strict validation.

    I9 requires: if ToolCallComplete is present, Finish.reason must be
    'tool_calls'.  With reason='stop' the validator rejects the stream.
    """
    events_in = [
        ToolCallComplete(
            index=0,
            id="call_1",
            name="echo",
            arguments_json='{"msg":"hello"}',
        ),
        Finish(reason="stop"),
        Done(),
    ]
    frames = _SER.serialize_events(events_in)
    parsed = parse_sse_events(frames)

    # Validator should reject: tool call present but finish_reason != tool_calls
    with pytest.raises(AssertionError, match="I9"):
        assert_stream_protocol_valid(parsed, profile="strict")


# ---------------------------------------------------------------------------
# test_module_level_functions
# ---------------------------------------------------------------------------


def test_module_level_functions():
    """Module-level serialize_event / serialize_events work."""
    event = AssistantTextDelta(delta="test")
    frame = serialize_event(event)
    assert isinstance(frame, bytes)
    assert b"test" in frame

    frames = serialize_events([
        AssistantTextDelta(delta="a"),
        Finish(reason="stop"),
        Done(),
    ])
    assert len(frames) == 3
