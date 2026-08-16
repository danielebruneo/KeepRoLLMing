"""Unit tests for StreamParser — V2 SSE → StreamEvent parser.

Verifies:
- AssistantTextDelta parsing
- Finish parsing
- Done parsing
- Done with whitespace
- Keepalive comment parsing
- Multiple events per chunk
- Frame split across chunks
- Unicode / quotes / newline / backslash preservation
- Finish reason tool_calls
- Invalid JSON does not crash
"""

from __future__ import annotations

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Done,
    Finish,
    Keepalive,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallDelta,
)
from keeprollming.streaming.parser import StreamParser

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PARSER = StreamParser()


# ---------------------------------------------------------------------------
# test_parse_assistant_text_delta
# ---------------------------------------------------------------------------


def test_parse_assistant_text_delta():
    """Single AssistantTextDelta("Hello") parses correctly."""
    chunk = b'data: {"choices":[{"delta":{"role":"assistant","content":"Hello"}}]}\n\n'
    events = _PARSER.parse_sync([chunk])

    text_deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_deltas) == 1
    assert text_deltas[0].delta == "Hello"


def test_parse_preserves_openai_response_envelope():
    """Response id/model/created survive parsing through the Finish frame."""
    chunk = (
        b'data: {"id":"chatcmpl-response_1","created":123,"model":"test-model",'
        b'"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"id":"chatcmpl-response_1","created":123,"model":"test-model",'
        b'"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    )
    events = StreamParser().parse_sync([chunk])

    text_event = next(event for event in events if isinstance(event, AssistantTextDelta))
    finish_event = next(event for event in events if isinstance(event, Finish))
    for event in (text_event, finish_event):
        assert event.event_id == "chatcmpl-response_1"
        assert event.metadata == {"created": 123, "model": "test-model"}


# ---------------------------------------------------------------------------
# test_parse_finish
# ---------------------------------------------------------------------------


def test_parse_finish():
    """Finish reason 'stop' parses to Finish(reason='stop')."""
    chunk = b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    events = _PARSER.parse_sync([chunk])

    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"


# ---------------------------------------------------------------------------
# test_parse_done
# ---------------------------------------------------------------------------


def test_parse_done():
    """data: [DONE] parses to Done."""
    chunk = b"data: [DONE]\n\n"
    events = _PARSER.parse_sync([chunk])

    dones = [e for e in events if isinstance(e, Done)]
    assert len(dones) == 1


# ---------------------------------------------------------------------------
# test_parse_done_with_whitespace
# ---------------------------------------------------------------------------


def test_parse_done_with_whitespace():
    """[DONE] with surrounding whitespace still parses to Done."""
    chunk = b"data:   [DONE]  \n\n"
    events = _PARSER.parse_sync([chunk])

    dones = [e for e in events if isinstance(e, Done)]
    assert len(dones) == 1


# ---------------------------------------------------------------------------
# test_parse_keepalive_comment
# ---------------------------------------------------------------------------


def test_parse_keepalive_comment():
    """SSE comment ': keepalive' parses to Keepalive."""
    chunk = b": keepalive\n\n"
    events = _PARSER.parse_sync([chunk])

    keepalives = [e for e in events if isinstance(e, Keepalive)]
    assert len(keepalives) == 1


# ---------------------------------------------------------------------------
# test_parse_multiple_events_per_chunk
# ---------------------------------------------------------------------------


def test_parse_multiple_events_per_chunk():
    """Multiple SSE frames in one chunk parse correctly.

    Content deltas preserve their upstream frame boundaries, and Finish is
    emitted after both deltas.
    """
    chunk = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" World"}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    )
    events = _PARSER.parse_sync([chunk])

    text_deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert [event.delta for event in text_deltas] == ["Hello", " World"]

    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"


# ---------------------------------------------------------------------------
# test_parse_frame_split_across_chunks
# ---------------------------------------------------------------------------


def test_parse_frame_split_across_chunks():
    """SSE frame split across chunks parses correctly.

    The split occurs mid-string inside the JSON value.  The reconstructed
    frame must be valid JSON for the parser to succeed.

    Chunk 1:  data: {"choices":[{"delta":{"content":"Hello
    Chunk 2:  World"}}]}\n\n
    Reconstructed: data: {"choices":[{"delta":{"content":"Hello World"}}]}\n\n
    """
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hello',
        b' World"}}]}\n\n',
    ]
    events = _PARSER.parse_sync(chunks)

    text_deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_deltas) == 1
    assert text_deltas[0].delta == "Hello World"


# ---------------------------------------------------------------------------
# test_parse_unicode_quotes_newline_backslash
# ---------------------------------------------------------------------------


def test_parse_unicode_quotes_newline_backslash():
    """Content with unicode, quotes, newline, backslash is preserved exactly."""
    content = 'Hello "world"\n日本語 \\escaped\\'
    chunk = f'data: {{\'choices\':[{{\'delta\':{{\'content\':\'' + content + r'\'}}}}]}}}\n\n'
    # Use json.dumps to ensure valid JSON
    import json
    payload = json.dumps({"choices": [{"delta": {"content": content}}]})
    chunk = f"data: {payload}\n\n".encode("utf-8")

    events = _PARSER.parse_sync([chunk])

    text_deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_deltas) == 1
    assert text_deltas[0].delta == content


# ---------------------------------------------------------------------------
# test_parse_finish_reason_tool_calls
# ---------------------------------------------------------------------------


def test_parse_finish_reason_tool_calls():
    """Finish reason 'tool_calls' parses to Finish(reason='tool_calls')."""
    chunk = b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
    events = _PARSER.parse_sync([chunk])

    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "tool_calls"


# ---------------------------------------------------------------------------
# test_invalid_json_does_not_crash
# ---------------------------------------------------------------------------


def test_invalid_json_does_not_crash():
    """Invalid JSON frame is silently skipped; parser does not crash."""
    chunk = b"data: {invalid json}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"OK\"}}]}\n\n"
    events = _PARSER.parse_sync([chunk])

    text_deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_deltas) == 1
    assert text_deltas[0].delta == "OK"


# ---------------------------------------------------------------------------
# test_parse_reasoning_text_delta
# ---------------------------------------------------------------------------


def test_parse_reasoning_text_delta():
    """ReasoningTextDelta parses correctly."""
    chunk = b'data: {"choices":[{"delta":{"reasoning_content":"Let me think..."}}]}\n\n'
    events = _PARSER.parse_sync([chunk])

    reasoning = [e for e in events if isinstance(e, ReasoningTextDelta)]
    assert len(reasoning) == 1
    assert reasoning[0].delta == "Let me think..."


# ---------------------------------------------------------------------------
# test_parse_tool_call_delta
# ---------------------------------------------------------------------------


def test_parse_tool_call_delta():
    """ToolCallDelta parses correctly."""
    chunk = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"echo","arguments":"hi"}}]}}]}\n\n'
    events = _PARSER.parse_sync([chunk])

    tc_deltas = [e for e in events if isinstance(e, ToolCallDelta)]
    assert len(tc_deltas) == 1
    assert tc_deltas[0].index == 0
    assert tc_deltas[0].id == "call_1"
    assert tc_deltas[0].name == "echo"
    assert tc_deltas[0].arguments_delta == "hi"


# ---------------------------------------------------------------------------
# test_parser_does_not_emit_tool_call_complete
# ---------------------------------------------------------------------------


def test_parser_does_not_emit_tool_call_complete():
    """Parser does NOT emit ToolCallComplete in Phase 1."""
    from keeprollming.streaming.events import ToolCallComplete

    chunk = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"echo","arguments":"hi"}}]}}]}\n\n'
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
    )
    events = _PARSER.parse_sync([chunk])

    tc_complete = [e for e in events if isinstance(e, ToolCallComplete)]
    assert len(tc_complete) == 0, "Parser should not emit ToolCallComplete in Phase 1"


# ---------------------------------------------------------------------------
# test_parse_content_then_finish_in_single_frame
# ---------------------------------------------------------------------------


def test_parse_content_then_finish_in_single_frame():
    """Content and finish_reason in the same delta: content is emitted, then Finish."""
    chunk = b'data: {"choices":[{"delta":{"content":"Hello","finish_reason":"stop"}}]}\n\n'
    events = _PARSER.parse_sync([chunk])

    text_deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(text_deltas) == 1
    assert text_deltas[0].delta == "Hello"
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"


# ---------------------------------------------------------------------------
# test_parse_empty_content
# ---------------------------------------------------------------------------


def test_parse_empty_content():
    """Empty string content produces AssistantTextDelta with empty delta."""
    chunk = b'data: {"choices":[{"delta":{"content":""}}]}\n\n'
    events = _PARSER.parse_sync([chunk])

    text_deltas = [e for e in events if isinstance(e, AssistantTextDelta)]
    assert len(text_deltas) == 1
    assert text_deltas[0].delta == ""


# ---------------------------------------------------------------------------
# test_parser_still_emits_tool_call_delta_only_not_complete
# ---------------------------------------------------------------------------


def test_parser_still_emits_tool_call_delta_only():
    """Parser emits ToolCallDelta but NOT ToolCallComplete."""
    import json

    args = json.dumps({"msg": "hi"})
    inner = {"index": 0, "id": "call_1", "function": {"name": "echo", "arguments": args}}
    delta = {"tool_calls": [inner]}
    obj = {"choices": [{"delta": delta}]}
    chunk = f"data: {json.dumps(obj)}\n\n".encode("utf-8")

    events = _PARSER.parse_sync([chunk])

    from keeprollming.streaming.events import ToolCallDelta, ToolCallComplete

    tc_deltas = [e for e in events if isinstance(e, ToolCallDelta)]
    tc_completes = [e for e in events if isinstance(e, ToolCallComplete)]

    assert len(tc_deltas) == 1
    assert tc_deltas[0].name == "echo"
    assert len(tc_completes) == 0, "Parser must NOT emit ToolCallComplete"


def test_parser_preserves_tool_call_after_early_finish_marker():
    """An upstream's early terminal marker must not discard a later call."""
    chunks = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"first","function":{"name":"one","arguments":"{}"}}]}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b'data: {"choices":[{"delta":{"reasoning_content":"Need one more call."}}]}\n\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"second","function":{"name":"two","arguments":"{}"}}]}}]}\n\n',
    ]

    events = _PARSER.parse_sync(chunks)
    from keeprollming.streaming.events import ToolCallDelta

    assert [event.name for event in events if isinstance(event, ToolCallDelta)] == ["one", "two"]
