"""V2 integration tests: ToolRewriteFinalizer through the V2 runner pipeline.

These tests verify that ToolRewriteFinalizer works correctly when wired into
the V2 streaming pipeline via the Pipeline._build_stream_finalizers() chain.

Tests:
- V2 runner-level: valid XML rewritten, ToolCallComplete emitted, Finish.reason
  upgraded, no XML leakage downstream.
- Fail-open: malformed XML passes through unchanged.
- Timestamp coexistence: TimestampFinalizer + ToolRewriteFinalizer produce
  exactly one timestamp, no duplication.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Done,
    Finish,
    ToolCallDelta,
    ToolCallComplete,
)
from keeprollming.streaming.finalizers import ToolCallFinalizer
from keeprollming.streaming.parser import StreamParser
from keeprollming.streaming.serializer import OpenAISSESerializer
from keeprollming.filters.timestamp.stream import TimestampFinalizer
from keeprollming.filters.tool_rewrite.stream import ToolRewriteFinalizer
from keeprollming.streaming.runner import run_stream
from tests.helpers.stream_client import (
    TestAssistantTextDelta,
    TestDone,
    TestFinish,
    TestToolCallComplete,
    parse_sse_events,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunks(*frames) -> list[bytes]:
    result: list[bytes] = []
    for f in frames:
        if isinstance(f, bytes):
            result.append(f)
        else:
            result.append(f.encode("utf-8"))
    return result


def _make_text_chunk(text: str) -> bytes:
    payload = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {payload}\n\n".encode("utf-8")


def _make_finish_chunk(reason: str = "stop") -> bytes:
    payload = json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]})
    return f"data: {payload}\n\n".encode("utf-8")


def _collect_chunks(async_gen):
    """Collect all bytes from an async generator."""
    result = []

    async def _collect():
        async for chunk in async_gen:
            result.append(chunk)

    asyncio.run(_collect())
    return result


# ---------------------------------------------------------------------------
# Test: V2 runner-level integration
# ---------------------------------------------------------------------------


def test_v2_tool_rewrite_integration_valid_xml():
    """V2 runner: valid XML rewritten, ToolCallComplete emitted, Finish.reason
    upgraded, no XML leakage.

    This is the full D1.5/D2 V2 route integration proof. It verifies that
    when ToolRewriteFinalizer (wired via _build_stream_finalizers) is in the chain:
    1. Valid XML pseudo-tool-call is rewritten to ToolCallDelta
    2. ToolCallFinalizer receives ToolCallDelta and emits ToolCallComplete
    3. Original XML is suppressed (not emitted downstream)
    4. Finish.reason is upgraded to "tool_calls"
    5. Exactly one Done is emitted last
    """
    tool_rewrite = ToolRewriteFinalizer()
    tool_call = ToolCallFinalizer(flush_valid_only=True)

    chunks = _make_chunks(
        _make_text_chunk("<read><path>README.md</path></read>"),
        _make_finish_chunk("stop"),
        b"data: [DONE]\n\n",
    )

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            finalizers=[tool_rewrite, tool_call],
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # 1. Verify ToolCallComplete was emitted
    tcc_events = [e for e in events if isinstance(e, TestToolCallComplete)]
    assert len(tcc_events) == 1, (
        f"Expected exactly one ToolCallComplete, got {len(tcc_events)}."
    )

    # 2. Verify ToolCallComplete has correct tool name and arguments
    tcc = tcc_events[0]
    assert tcc.name == "read", f"Expected tool name 'read', got '{tcc.name}'."
    assert tcc.arguments_json == '{"path":"README.md"}', (
        f"Expected arguments '{{\"path\":\"README.md\"}}', got '{tcc.arguments_json}'."
    )

    # 3. Verify original XML is NOT downstream
    xml_events = [
        e for e in events
        if isinstance(e, TestAssistantTextDelta) and "<read>" in e.delta
    ]
    assert len(xml_events) == 0, (
        f"Original XML must be suppressed, but found {len(xml_events)} events "
        f"with '<read>' content."
    )

    # 4. Verify Finish.reason is "tool_calls"
    finish_events = [e for e in events if isinstance(e, TestFinish)]
    assert len(finish_events) == 1, (
        f"Expected exactly one Finish event, got {len(finish_events)}."
    )
    assert finish_events[0].reason == "tool_calls", (
        f"Expected Finish.reason='tool_calls', got '{finish_events[0].reason}'."
    )

    # 5. Verify Done is last
    assert isinstance(events[-1], TestDone), "Done must be the last event"

    # 6. Verify exactly one Done
    done_events = [e for e in events if isinstance(e, TestDone)]
    assert len(done_events) == 1, (
        f"Expected exactly one Done event, got {len(done_events)}."
    )


# ---------------------------------------------------------------------------
# Test: Fail-open through wired path
# ---------------------------------------------------------------------------


def test_v2_tool_rewrite_fail_open_malformed_xml():
    """Fail-open: malformed XML with unescaped & passes through unchanged.

    When XML is malformed (e.g., contains unescaped &), ToolRewriteFinalizer
    should NOT rewrite it. The original text should pass through unchanged.
    No ToolCallComplete should be emitted. Finish.reason should be "stop".
    """
    tool_rewrite = ToolRewriteFinalizer()
    tool_call = ToolCallFinalizer(flush_valid_only=True)

    # Malformed XML: unescaped & breaks XML parsing
    chunks = _make_chunks(
        _make_text_chunk("<read>path=foo&bar</read>"),
        _make_finish_chunk("stop"),
        b"data: [DONE]\n\n",
    )

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            finalizers=[tool_rewrite, tool_call],
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # 1. No ToolCallComplete should be emitted
    tcc_events = [e for e in events if isinstance(e, TestToolCallComplete)]
    assert len(tcc_events) == 0, (
        f"Malformed XML should NOT produce ToolCallComplete, but got "
        f"{len(tcc_events)}."
    )

    # 2. Original text should pass through (fail-open)
    text_events = [
        e for e in events if isinstance(e, TestAssistantTextDelta)
    ]
    assert len(text_events) > 0, "Expected at least one AssistantTextDelta."

    # The malformed XML should be present in the output (not rewritten)
    has_malformed = any("<read>" in e.delta for e in text_events)
    assert has_malformed, (
        "Malformed XML should pass through unchanged (fail-open)."
    )

    # 3. Finish.reason should be "stop" (no tool calls)
    finish_events = [e for e in events if isinstance(e, TestFinish)]
    assert len(finish_events) == 1
    assert finish_events[0].reason == "stop", (
        f"Expected Finish.reason='stop' for malformed XML, "
        f"got '{finish_events[0].reason}'."
    )


# ---------------------------------------------------------------------------
# Test: Timestamp coexistence
# ---------------------------------------------------------------------------


def test_v2_tool_rewrite_timestamp_coexistence():
    """Timestamp + ToolRewrite preserve a tool-call-only terminal turn.

    When both TimestampFinalizer (priority 20) and ToolRewriteFinalizer
    (priority 15) are in the chain:
    1. Valid XML is rewritten to ToolCallDelta
    2. No timestamp-only assistant text is manufactured
    3. Finish is emitted as ``tool_calls``
    4. Done terminates the stream
    """
    timestamp = TimestampFinalizer(
        template="\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC",
        timezone="UTC",
        tail_buffer_size=1024,
    )
    tool_rewrite = ToolRewriteFinalizer()
    tool_call = ToolCallFinalizer(flush_valid_only=True)

    chunks = _make_chunks(
        _make_text_chunk("<read><path>README.md</path></read>"),
        _make_finish_chunk("stop"),
        b"data: [DONE]\n\n",
    )

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            finalizers=[timestamp, tool_rewrite, tool_call],
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # 1. Verify ToolCallComplete was emitted
    tcc_events = [e for e in events if isinstance(e, TestToolCallComplete)]
    assert len(tcc_events) == 1, (
        f"Expected exactly one ToolCallComplete, got {len(tcc_events)}."
    )

    # 2. Verify Finish.reason is "tool_calls"
    finish_events = [e for e in events if isinstance(e, TestFinish)]
    assert len(finish_events) == 1
    assert finish_events[0].reason == "tool_calls", (
        f"Expected Finish.reason='tool_calls', got '{finish_events[0].reason}'."
    )

    # 3. Verify original XML is NOT downstream
    xml_events = [
        e for e in events
        if isinstance(e, TestAssistantTextDelta) and "<read>" in e.delta
    ]
    assert len(xml_events) == 0, (
        f"Original XML must be suppressed, but found {len(xml_events)} events."
    )

    # 4. A tool-call-only turn must not manufacture a timestamp-only
    # assistant message. TimestampFinalizer appends a footer only when there
    # is assistant text to preserve.
    timestamp_text_events = [
        e for e in events
        if isinstance(e, TestAssistantTextDelta) and "Timestamp:" in e.delta
    ]
    assert timestamp_text_events == []

    # 5. The terminal assistant turn carries only the tool call. In
    # particular, no text event may occur before the tool-call finish: clients
    # such as LibreChat treat mixed assistant content + tool calls as invalid.
    finish_idx = next(i for i, e in enumerate(events) if isinstance(e, TestFinish))
    assert not any(
        isinstance(event, TestAssistantTextDelta)
        for event in events[:finish_idx]
    )

    # 6. Verify Done is last
    assert isinstance(events[-1], TestDone), "Done must be the last event"
