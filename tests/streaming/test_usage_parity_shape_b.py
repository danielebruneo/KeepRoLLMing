"""Tests for Shape B usage parity fix (finish_reason before usage chunk).

Verifies that the V2 streaming pipeline correctly captures usage metadata
when upstream sends finish_reason before the usage chunk.

These tests correspond to Regression 1 from INVESTIGATION-092-RUNTIME-PARITY-AUDIT-001.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from keeprollming.streaming.parser import StreamParser
from keeprollming.streaming.serializer import OpenAISSESerializer
from keeprollming.streaming.runner import run_stream
from tests.helpers.stream_client import (
    TestFinish,
    TestDone,
    parse_sse_events,
    collect_assistant_text,
    assert_stream_protocol_valid,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_text_chunk(text: str) -> bytes:
    """Build a single SSE chunk with assistant text."""
    payload = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {payload}\n\n".encode("utf-8")


def _make_finish_chunk(reason: str = "stop") -> bytes:
    """Build a single SSE chunk with finish_reason."""
    payload = json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]})
    return f"data: {payload}\n\n".encode("utf-8")


def _make_usage_chunk(usage: dict) -> bytes:
    """Build a single SSE chunk with usage metadata (empty choices)."""
    payload = json.dumps({"choices": [], "usage": usage})
    return f"data: {payload}\n\n".encode("utf-8")


def _make_finish_with_usage_chunk(reason: str = "stop", usage: dict = None) -> bytes:
    """Build a single SSE chunk with finish_reason and usage in same chunk."""
    obj = {"choices": [{"delta": {}, "finish_reason": reason}]}
    if usage is not None:
        obj["usage"] = usage
    payload = json.dumps(obj)
    return f"data: {payload}\n\n".encode("utf-8")


def _make_done_chunk() -> bytes:
    """Build a [DONE] SSE chunk."""
    return b"data: [DONE]\n\n"


def _collect_chunks(async_gen) -> list[bytes]:
    """Consume an async iterator and collect all chunks."""
    result: list[bytes] = []

    async def _collect():
        try:
            while True:
                chunk = await async_gen.__anext__()
                result.append(chunk)
        except StopAsyncIteration:
            pass

    asyncio.run(_collect())
    return result


# ---------------------------------------------------------------------------
# test_shape_b_finish_before_usage
# ---------------------------------------------------------------------------


def test_shape_b_finish_before_usage():
    """finish_reason arrives before usage chunk → Finish emitted with usage.

    This is the core Shape B parity fix: upstream sends finish_reason first,
    then usage in a subsequent chunk. The runner must capture the usage and
    include it in the Finish event.
    """
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    chunks = [
        _make_text_chunk("Hello"),
        _make_finish_chunk("stop"),
        _make_usage_chunk(usage),
        _make_done_chunk(),
    ]

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # Protocol valid
    assert_stream_protocol_valid(events, profile="strict")

    # Content preserved
    text = collect_assistant_text(events)
    assert text == "Hello"

    # Finish has usage from later chunk
    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"
    assert finishes[0].usage == usage, (
        f"Shape B parity: Finish.usage must be from later chunk, got {finishes[0].usage}"
    )

    # Done present
    dones = [e for e in events if isinstance(e, TestDone)]
    assert len(dones) == 1


# ---------------------------------------------------------------------------
# test_shape_b_finish_and_usage_same_chunk
# ---------------------------------------------------------------------------


def test_shape_b_finish_and_usage_same_chunk():
    """finish_reason and usage in same chunk → Finish emitted with that usage.

    No regression: when finish_reason and usage arrive together, usage is
    captured correctly.
    """
    usage = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}

    chunks = [
        _make_text_chunk("Hello"),
        _make_finish_with_usage_chunk("stop", usage),
        _make_done_chunk(),
    ]

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # Protocol valid
    assert_stream_protocol_valid(events, profile="strict")

    # Content preserved
    text = collect_assistant_text(events)
    assert text == "Hello"

    # Finish has usage from same chunk
    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"
    assert finishes[0].usage == usage, (
        f"Same-chunk: Finish.usage must match, got {finishes[0].usage}"
    )


# ---------------------------------------------------------------------------
# test_shape_c_usage_before_finish
# ---------------------------------------------------------------------------


def test_shape_c_usage_before_finish():
    """usage arrives before finish_reason → Finish emitted with that usage.

    Shape C (no regression): usage chunk arrives before finish_reason.
    Usage is captured and included in Finish.
    """
    usage = {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45}

    chunks = [
        _make_text_chunk("Hello"),
        _make_usage_chunk(usage),
        _make_finish_chunk("stop"),
        _make_done_chunk(),
    ]

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # Protocol valid
    assert_stream_protocol_valid(events, profile="strict")

    # Content preserved
    text = collect_assistant_text(events)
    assert text == "Hello"

    # Finish has usage from earlier chunk
    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"
    assert finishes[0].usage == usage, (
        f"Shape C: Finish.usage must match earlier chunk, got {finishes[0].usage}"
    )


# ---------------------------------------------------------------------------
# test_shape_b_latest_usage_wins
# ---------------------------------------------------------------------------


def test_shape_b_latest_usage_wins():
    """Multiple usage chunks → last one before stream end wins.

    If multiple usage chunks arrive (before or after finish_reason), the
    last one observed before stream exhaustion is used.
    """
    usage_1 = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    usage_2 = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}

    chunks = [
        _make_text_chunk("Hello"),
        _make_usage_chunk(usage_1),
        _make_finish_chunk("stop"),
        _make_usage_chunk(usage_2),  # Latest usage after finish_reason
        _make_done_chunk(),
    ]

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # Protocol valid
    assert_stream_protocol_valid(events, profile="strict")

    # Finish has latest usage
    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].usage == usage_2, (
        f"Latest usage wins: expected {usage_2}, got {finishes[0].usage}"
    )


# ---------------------------------------------------------------------------
# test_shape_b_no_usage
# ---------------------------------------------------------------------------


def test_shape_b_no_usage():
    """finish_reason without any usage → Finish.usage is None.

    When no usage chunk arrives, Finish.usage is None (not an error).
    """
    chunks = [
        _make_text_chunk("Hello"),
        _make_finish_chunk("stop"),
        _make_done_chunk(),
    ]

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # Protocol valid
    assert_stream_protocol_valid(events, profile="strict")

    # Finish.usage is None
    finishes = [e for e in events if isinstance(e, TestFinish)]
    assert len(finishes) == 1
    assert finishes[0].usage is None


# ---------------------------------------------------------------------------
# test_parser_standalone_shape_b
# ---------------------------------------------------------------------------


def test_parser_standalone_shape_b():
    """Standalone StreamParser also captures usage after finish_reason.

    The parser's parse_sync/parse methods emit Finish at stream end with
    accumulated usage, preserving backward compatibility for tests.
    """
    from keeprollming.streaming.events import Finish

    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    chunks = [
        _make_text_chunk("Hello"),
        _make_finish_chunk("stop"),
        _make_usage_chunk(usage),
        _make_done_chunk(),
    ]

    parser = StreamParser()
    events = parser.parse_sync(chunks)

    # Finish emitted with usage from later chunk
    finishes = [e for e in events if isinstance(e, Finish)]
    assert len(finishes) == 1
    assert finishes[0].reason == "stop"
    assert finishes[0].usage == usage, (
        f"Parser standalone: Finish.usage must be from later chunk, got {finishes[0].usage}"
    )
