"""Protocol invariant tests for the streaming mini-client/parser.

These tests validate the ``tests/helpers/stream_client.py`` parsing and
assertion logic against canonical downstream SSE shapes.
"""

from __future__ import annotations

import json

import pytest

from tests.helpers.stream_client import (
    TestAssistantTextDelta,
    TestDone,
    TestFinish,
    TestToolCallComplete,
    TestToolCallDelta,
    collect_assistant_text,
    collect_tool_calls,
    parse_sse_events,
    assert_stream_protocol_valid,
)

# ---------------------------------------------------------------------------
# Helper: build a minimal SSE content chunk
# ---------------------------------------------------------------------------

def _content_chunk(text: str, finish_reason: str | None = None, index: int = 0) -> bytes:
    """Return a downstream OpenAI-compatible SSE data chunk."""
    obj: dict = {
        "id": "test-1",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [
            {
                "index": index,
                "delta": {"content": text} if text else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


def _tool_call_chunk(
    index: int = 0,
    id: str | None = None,
    name: str | None = None,
    arguments: str = "",
    finish_reason: str | None = None,
) -> bytes:
    """Return a downstream SSE data chunk carrying a tool_call delta."""
    fn: dict = {}
    if name is not None:
        fn["name"] = name
    if arguments:
        fn["arguments"] = arguments
    delta: dict = {"tool_calls": [{"index": index, "function": fn}]}
    if id:
        delta["tool_calls"][0]["id"] = id
    obj: dict = {
        "id": "test-1",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return b"data: " + json.dumps(obj).encode() + b"\n\n"


def _reasoning_chunk(text: str) -> bytes:
    obj: dict = {
        "id": "test-1",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": {"reasoning_content": text},
                "finish_reason": None,
            }
        ],
    }
    return b"data: " + json.dumps(obj).encode() + b"\n\n"

# ---------------------------------------------------------------------------
# Test 1: normal content + finish + [DONE]
# ---------------------------------------------------------------------------

def test_mini_client_parses_normal_content_finish_done() -> None:
    """Input SSE: content chunks → finish_reason stop → [DONE].

    Assert collected text correct, exactly one finish, exactly one done,
    done last, validator passes.
    """
    chunks = [
        _content_chunk("Hello "),
        _content_chunk("world!"),
        _content_chunk("", finish_reason="stop"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    text = collect_assistant_text(events)
    assert text == "Hello world!"

    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"

    dones = [e for e in events if isinstance(e, TestDone)]
    assert len(dones) == 1
    assert isinstance(events[-1], TestDone)

    assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 2: content after finish_reason
# ---------------------------------------------------------------------------

def test_validator_rejects_content_after_finish() -> None:
    """Input SSE: content → finish → content → [DONE]."""
    chunks = [
        _content_chunk("Hello"),
        _content_chunk("", finish_reason="stop"),
        _content_chunk(" after"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    with pytest.raises(AssertionError, match="I4"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 3: [DONE] not last
# ---------------------------------------------------------------------------

def test_validator_rejects_done_not_last() -> None:
    """Input SSE: content → [DONE] → content."""
    chunks = [
        _content_chunk("Hello"),
        b"data: [DONE]\n\n",
        _content_chunk(" after"),
    ]
    events = parse_sse_events(chunks)

    with pytest.raises(AssertionError, match="I2"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 4: duplicate finish_reason
# ---------------------------------------------------------------------------

def test_validator_rejects_duplicate_finish() -> None:
    """Input SSE: content → finish stop → finish stop → [DONE]."""
    chunks = [
        _content_chunk("Hello"),
        _content_chunk("", finish_reason="stop"),
        _content_chunk("", finish_reason="stop"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    with pytest.raises(AssertionError, match="I1"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 5: duplicate [DONE]
# ---------------------------------------------------------------------------

def test_validator_rejects_duplicate_done() -> None:
    """Input SSE: content → finish → [DONE] → [DONE]."""
    chunks = [
        _content_chunk("Hello"),
        _content_chunk("", finish_reason="stop"),
        b"data: [DONE]\n\n",
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    with pytest.raises(AssertionError, match="I12"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 6: duplicate timestamp footer
# ---------------------------------------------------------------------------

def test_validator_rejects_duplicate_timestamp_footer() -> None:
    """Input SSE: content with two timestamp footers → finish → [DONE]."""
    chunks = [
        _content_chunk(
            "Hello\n\n---\nTimestamp: 2026-01-01 00:00:00 UTC\n\n---\nTimestamp: 2026-01-01 01:00:00 UTC"
        ),
        _content_chunk("", finish_reason="stop"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    with pytest.raises(AssertionError, match="I6"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 7: single timestamp before finish (should pass)
# ---------------------------------------------------------------------------

def test_validator_accepts_single_timestamp_before_finish() -> None:
    """Input SSE: content with one timestamp footer → finish → [DONE]."""
    chunks = [
        _content_chunk(
            "Hello\n\n---\nTimestamp: 2026-01-01 00:00:00 UTC"
        ),
        _content_chunk("", finish_reason="stop"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 8: tool_call arguments must be valid JSON when finished
# ---------------------------------------------------------------------------

def test_tool_call_arguments_must_be_valid_json_when_finished() -> None:
    """Input SSE: tool_call chunk with invalid function.arguments → finish tool_calls → [DONE]."""
    chunks = [
        _tool_call_chunk(
            index=0,
            id="call_123",
            name="run_command",
            arguments='{"command": "ls -la"',  # invalid JSON
        ),
        _content_chunk("", finish_reason="tool_calls"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    with pytest.raises(AssertionError, match="I8"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 9: tool_call requires finish_reason=tool_calls
# ---------------------------------------------------------------------------

def test_tool_call_requires_finish_reason_tool_calls() -> None:
    """Input SSE: tool_call chunk → finish_reason stop → [DONE]."""
    chunks = [
        _tool_call_chunk(
            index=0,
            id="call_123",
            name="run_command",
            arguments='{"command": "ls -la"}',
        ),
        _content_chunk("", finish_reason="stop"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    with pytest.raises(AssertionError, match="I9"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test 10: content and tool_call in same delta rejected in strict profile
# ---------------------------------------------------------------------------

def test_content_and_tool_call_same_delta_rejected_in_strict_profile() -> None:
    """Input SSE: one chunk with both delta.content and delta.tool_calls → finish tool_calls → [DONE]."""
    chunk_bytes = b'data: {"id":"test-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"content":"Hello ","tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"run_command","arguments":"{}"}}]},"finish_reason":null}]}'
    chunk_bytes += b"\n\n"

    chunks = [
        chunk_bytes,
        _content_chunk("", finish_reason="tool_calls"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    # Verify the parser correctly splits content and tool_call into separate events
    text = collect_assistant_text(events)
    assert text == "Hello "

    tool_calls = collect_tool_calls(events)
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "run_command"

    # In strict profile, content and tool_call in the same original delta should fail
    with pytest.raises(AssertionError, match="I10"):
        assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test D: multiple SSE frames in one chunk
# ---------------------------------------------------------------------------

def test_parse_multiple_sse_frames_in_one_chunk() -> None:
    """Two SSE frames inside a single chunk separated by blank line."""
    frame1 = _content_chunk("Hello ")
    frame2 = _content_chunk("world!", finish_reason="stop")
    # Join with actual blank line to simulate two frames in one chunk
    single_chunk = frame1 + b"\n\n" + frame2

    events = parse_sse_events([single_chunk, b"data: [DONE]\n\n"])

    text = collect_assistant_text(events)
    assert text == "Hello world!"

    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"

    assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test E: SSE frame split across chunks
# ---------------------------------------------------------------------------

def test_parse_sse_frame_split_across_chunks() -> None:
    """A single SSE frame is split across two input chunks."""
    # First chunk: content frame up to (but not including) the closing }
    half1 = b'data: {"id":"t1","object":"chat.completion.chunk","created":1700000000,"model":"m","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]'
    # Second chunk: closing } + blank line + finish + blank line + DONE
    half2 = b'}\n\ndata: {"id":"t1","object":"chat.completion.chunk","created":1700000000,"model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    events = parse_sse_events([half1, half2])

    text = collect_assistant_text(events)
    assert text == "Hello "

    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"

    dones = [e for e in events if isinstance(e, TestDone)]
    assert len(dones) == 1
    assert isinstance(events[-1], TestDone)

    assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test F: [DONE] with extra whitespace
# ---------------------------------------------------------------------------

def test_parse_done_split_or_with_extra_whitespace() -> None:
    """[DONE] with surrounding whitespace or split across chunks."""
    # Chunk 1: partial [DONE]
    chunk1 = b'data: [DO'
    # Chunk 2: NE] + two newlines
    chunk2 = b'NE]\n\n'

    events = parse_sse_events([chunk1, chunk2])

    dones = [e for e in events if isinstance(e, TestDone)]
    assert len(dones) == 1
    assert isinstance(events[-1], TestDone)


# ---------------------------------------------------------------------------
# Test A: chunk index tracks real SSE chunk
# ---------------------------------------------------------------------------

def test_chunk_index_tracks_real_sse_chunk() -> None:
    """Content in chunk 0, tool_call in chunk 1, finish in chunk 2, DONE in chunk 3.

    Assert their _source_chunk_index values are correct.
    Assert strict validator does NOT falsely reject content+tool_call when
    they came from different chunks.
    """
    # Chunk 0: content
    c0 = _content_chunk("Hello ")
    # Chunk 1: tool_call
    tc1 = _tool_call_chunk(
        index=0, id="call_123", name="run_command", arguments='{"cmd":"ls"}'
    )
    # Chunk 2: finish
    c2 = _content_chunk("", finish_reason="tool_calls")
    # Chunk 3: DONE
    done = b"data: [DONE]\n\n"

    events = parse_sse_events([c0, tc1, c2, done])

    # Verify chunk indices
    content_events = [e for e in events if isinstance(e, TestAssistantTextDelta)]
    assert len(content_events) == 1
    assert content_events[0]._source_chunk_index == 0

    tc_complete = [e for e in events if isinstance(e, TestToolCallComplete)]
    assert len(tc_complete) == 1
    # ToolCallComplete is emitted inline when finish is parsed (chunk 2)
    assert tc_complete[0]._source_chunk_index == 2

    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0]._source_chunk_index == 2

    dones = [e for e in events if isinstance(e, TestDone)]
    assert len(dones) == 1
    assert dones[0]._source_chunk_index == 3

    # Validator must pass — content and tool_call are in different chunks
    assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test B: tool_call_complete is before finish
# ---------------------------------------------------------------------------

def test_tool_call_complete_is_before_finish() -> None:
    """tool_call chunk → finish_reason tool_calls → DONE.

    Assert event order is ToolCallDelta, TestToolCallComplete, TestFinish, TestDone.
    Validator passes.
    """
    chunks = [
        _tool_call_chunk(
            index=0, id="call_abc", name="run_command", arguments='{"cmd":"ls"}'
        ),
        _content_chunk("", finish_reason="tool_calls"),
        b"data: [DONE]\n\n",
    ]
    events = parse_sse_events(chunks)

    # Verify canonical ordering
    tc_deltas = [e for e in events if isinstance(e, TestToolCallDelta)]
    tc_completes = [e for e in events if isinstance(e, TestToolCallComplete)]
    finishes = [e for e in events if isinstance(e, TestFinish)]
    dones = [e for e in events if isinstance(e, TestDone)]

    assert len(tc_deltas) == 1
    assert len(tc_completes) == 1
    assert len(finishes) == 1
    assert len(dones) == 1

    # ToolCallComplete must appear before TestFinish
    tc_complete_idx = next(i for i, e in enumerate(events) if isinstance(e, TestToolCallComplete))
    finish_idx = next(i for i, e in enumerate(events) if isinstance(e, TestFinish))
    assert tc_complete_idx < finish_idx, (
        f"TestToolCallComplete at {tc_complete_idx} must be before TestFinish at {finish_idx}"
    )

    # Validator must pass
    assert_stream_protocol_valid(events, profile="strict")


# ---------------------------------------------------------------------------
# Test C: delta and finish_reason in same chunk are both parsed
# ---------------------------------------------------------------------------

def test_delta_and_finish_reason_in_same_chunk_are_both_parsed() -> None:
    """One chunk with delta.content='Hello' and finish_reason='stop' → DONE.

    Assert collected text is 'Hello', finish reason is 'stop', validator passes.
    """
    chunk_bytes = b'data: {"id":"test-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":"stop"}]}'
    chunk_bytes += b"\n\n"

    chunks = [chunk_bytes, b"data: [DONE]\n\n"]
    events = parse_sse_events(chunks)

    text = collect_assistant_text(events)
    assert text == "Hello"

    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"

    assert_stream_protocol_valid(events, profile="strict")
