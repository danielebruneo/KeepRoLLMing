"""Tests for V2 streaming usage parity.

This module tests the V2 parser's ability to extract and preserve upstream
usage metadata (prompt_tokens, completion_tokens, total_tokens) in the
client-facing Finish event.

Supported shapes:
- Shape A: usage + finish_reason in the same chunk
- Shape C: usage before finish_reason (usage-only chunk, then finish chunk)

Unsupported (deferred):
- Shape B: finish_reason first, then usage-only chunk (late terminal metadata)

Policy:
- Standard `usage` = client-facing/OpenAI-compatible metadata
- Future `krm_usage` = cumulative actual execution accounting (out of scope)
- No retry aggregation in this phase
- Latest usage wins: if multiple usage chunks arrive before Finish, the last
  one observed is used.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from keeprollming.streaming.events import AssistantTextDelta, Done, Finish
from keeprollming.streaming.parser import StreamParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_chunk(obj: Dict[str, Any]) -> bytes:
    """Build an SSE data chunk from a dict."""
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode("utf-8")


def _done_chunk() -> bytes:
    """Build a [DONE] chunk."""
    return b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Shape A: usage + finish_reason in the same chunk
# ---------------------------------------------------------------------------


class TestShapeA_SameChunkUsage:
    """Shape A: usage and finish_reason in the same SSE chunk."""

    def test_shape_a_basic(self):
        """Shape A: usage + finish_reason in same chunk."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        done_events = [e for e in events if isinstance(e, Done)]
        assert len(finish_events) == 1, f"Expected 1 Finish, got {len(finish_events)}"
        assert len(done_events) == 1, f"Expected 1 Done, got {len(done_events)}"
        assert finish_events[0].reason == "stop"
        assert finish_events[0].usage == usage

    def test_shape_a_with_content(self):
        """Shape A: content + usage + finish_reason in same chunk."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk = _sse_chunk({
            "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": usage,
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        assistant_events = [e for e in events if isinstance(e, AssistantTextDelta)]
        finish_events = [e for e in events if isinstance(e, Finish)]
        done_events = [e for e in events if isinstance(e, Done)]
        assert len(assistant_events) == 1
        assert assistant_events[0].delta == "Hello"
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage
        assert len(done_events) == 1

    def test_shape_a_tool_calls_finish(self):
        """Shape A: tool_calls + usage + finish_reason=tool_calls."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk = _sse_chunk({
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"name": "test"}}]},
                "finish_reason": "tool_calls",
            }],
            "usage": usage,
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].reason == "tool_calls"
        assert finish_events[0].usage == usage


# ---------------------------------------------------------------------------
# Shape C: usage before finish_reason
# ---------------------------------------------------------------------------


class TestShapeC_UsageBeforeFinish:
    """Shape C: usage-only chunk arrives before finish_reason chunk."""

    def test_shape_c_basic(self):
        """Shape C: usage-only chunk, then finish chunk."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk1 = _sse_chunk({"choices": [], "usage": usage})
        chunk2 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events = parser.parse_sync([chunk1, chunk2, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        done_events = [e for e in events if isinstance(e, Done)]
        assert len(finish_events) == 1
        assert finish_events[0].reason == "stop"
        assert finish_events[0].usage == usage
        assert len(done_events) == 1

    def test_shape_c_with_content_between(self):
        """Shape C: usage, then content, then finish."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk1 = _sse_chunk({"choices": [], "usage": usage})
        chunk2 = _sse_chunk({
            "choices": [{"index": 0, "delta": {"content": "Hello"}}],
        })
        chunk3 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events = parser.parse_sync([chunk1, chunk2, chunk3, _done_chunk()])
        assistant_events = [e for e in events if isinstance(e, AssistantTextDelta)]
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(assistant_events) == 1
        assert assistant_events[0].delta == "Hello"
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage

    def test_shape_c_multiple_usage_chunks(self):
        """Shape C: multiple usage chunks before finish — latest wins."""
        parser = StreamParser()
        usage1 = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        usage2 = {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}
        chunk1 = _sse_chunk({"choices": [], "usage": usage1})
        chunk2 = _sse_chunk({"choices": [], "usage": usage2})
        chunk3 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events = parser.parse_sync([chunk1, chunk2, chunk3, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage2, "Latest usage should win"


# ---------------------------------------------------------------------------
# Shape B: finish_reason first, then usage-only chunk (UNSUPPORTED)
# ---------------------------------------------------------------------------


class TestShapeB_FinishBeforeUsage_Supported:
    """Shape B: finish_reason arrives before usage-only chunk.

    Shape B is now supported: Finish emission is deferred to stream end,
    so that usage arriving after finish_reason is captured correctly.
    """

    def test_shape_b_usage_captured(self):
        """Shape B: finish first, then usage — usage is captured (fixed)."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk1 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        chunk2 = _sse_chunk({"choices": [], "usage": usage})
        events = parser.parse_sync([chunk1, chunk2, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        done_events = [e for e in events if isinstance(e, Done)]
        assert len(finish_events) == 1
        assert finish_events[0].reason == "stop"
        assert finish_events[0].usage == usage, \
            "Shape B: usage after finish_reason must be captured"
        assert len(done_events) == 1


# ---------------------------------------------------------------------------
# No usage
# ---------------------------------------------------------------------------


class TestNoUsage:
    """Streams without usage metadata."""

    def test_no_usage_finish(self):
        """Finish without usage has usage=None."""
        parser = StreamParser()
        chunk = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].usage is None

    def test_no_usage_with_content(self):
        """Content stream without usage."""
        parser = StreamParser()
        chunk = _sse_chunk({
            "choices": [{"index": 0, "delta": {"content": "Hello"}}],
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        # No finish_reason in chunk, so no Finish event emitted
        assert len(finish_events) == 0


# ---------------------------------------------------------------------------
# Latest usage wins
# ---------------------------------------------------------------------------


class TestLatestUsageWins:
    """When multiple usage chunks arrive, the last one before Finish wins."""

    def test_latest_usage_wins(self):
        """Usage is overwritten by later chunks."""
        parser = StreamParser()
        usage1 = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        usage2 = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        chunk1 = _sse_chunk({"choices": [], "usage": usage1})
        chunk2 = _sse_chunk({"choices": [], "usage": usage2})
        chunk3 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events = parser.parse_sync([chunk1, chunk2, chunk3, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage2

    def test_usage_in_finish_chunk_overrides_previous(self):
        """Usage in finish chunk overrides earlier usage-only chunks."""
        parser = StreamParser()
        usage1 = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        usage2 = {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}
        chunk1 = _sse_chunk({"choices": [], "usage": usage1})
        chunk2 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage2,
        })
        events = parser.parse_sync([chunk1, chunk2, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage2


# ---------------------------------------------------------------------------
# Parser lifecycle: no usage leakage across streams
# ---------------------------------------------------------------------------


class TestParserLifecycle:
    """Parser state is reset between parse_sync calls (no usage leakage)."""

    def test_no_usage_leakage_across_parse_sync_calls(self):
        """Each parse_sync call starts with pending_usage=None."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

        # First stream: has usage
        chunk1 = _sse_chunk({"choices": [], "usage": usage})
        chunk2 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events1 = parser.parse_sync([chunk1, chunk2, _done_chunk()])
        finish_events1 = [e for e in events1 if isinstance(e, Finish)]
        assert finish_events1[0].usage == usage

        # Second stream: no usage
        chunk3 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events2 = parser.parse_sync([chunk3, _done_chunk()])
        finish_events2 = [e for e in events2 if isinstance(e, Finish)]
        assert finish_events2[0].usage is None, \
            "Usage from previous stream should not leak"

    def test_pending_usage_reset_after_finish(self):
        """pending_usage is reset after Finish is emitted."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

        # Stream with usage
        chunk1 = _sse_chunk({"choices": [], "usage": usage})
        chunk2 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events = parser.parse_sync([chunk1, chunk2, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert finish_events[0].usage == usage

        # Verify parser state is clean (no usage leakage)
        # by parsing a new stream without usage
        chunk3 = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        events2 = parser.parse_sync([chunk3, _done_chunk()])
        finish_events2 = [e for e in events2 if isinstance(e, Finish)]
        assert finish_events2[0].usage is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestUsageEdgeCases:
    """Edge cases for usage parsing."""

    def test_usage_with_reasoning(self):
        """Usage with reasoning_content."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk = _sse_chunk({
            "choices": [{
                "index": 0,
                "delta": {"reasoning_content": "Thinking..."},
                "finish_reason": "stop",
            }],
            "usage": usage,
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        reasoning_events = [e for e in events if e.__class__.__name__ == "ReasoningTextDelta"]
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(reasoning_events) == 1
        assert reasoning_events[0].delta == "Thinking..."
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage

    def test_usage_with_tool_calls(self):
        """Usage with tool_calls."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        chunk = _sse_chunk({
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"name": "test"}}]},
                "finish_reason": "tool_calls",
            }],
            "usage": usage,
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].reason == "tool_calls"
        assert finish_events[0].usage == usage

    def test_usage_with_cached_tokens_details(self):
        """Usage with prompt_tokens_details.cached_tokens."""
        parser = StreamParser()
        usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "total_tokens": 1100,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
        chunk = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage
        assert finish_events[0].usage["prompt_tokens_details"]["cached_tokens"] == 800

    def test_usage_partial_fields(self):
        """Usage with only some fields present."""
        parser = StreamParser()
        usage = {"prompt_tokens": 10}
        chunk = _sse_chunk({
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        })
        events = parser.parse_sync([chunk, _done_chunk()])
        finish_events = [e for e in events if isinstance(e, Finish)]
        assert len(finish_events) == 1
        assert finish_events[0].usage == usage
