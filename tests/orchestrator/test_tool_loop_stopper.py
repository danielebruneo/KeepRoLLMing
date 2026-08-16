"""
Tests for Tool Loop Stopper (TLS) filter.

Covers: detection, non-streaming intervention, streaming behavior, fallback, logging.
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List

import pytest

from keeprollming.orchestrator.filter import FilterExecutionContext
from keeprollming.filters.tool_loop_stopper.request import (
    ToolLoopStopperFilter,
    ToolLoopStopperConfig,
)


# ── Test Helpers ──────────────────────────────────────────────────────────────


def _make_conv_with_tool(
    function_name: str = "search_file",
    arguments: dict = None,
    tool_result: str = "found: /tmp/test.txt",
) -> List[Dict[str, Any]]:
    """Build a conversation with one tool call and its result."""
    if arguments is None:
        arguments = {"pattern": "*.py", "path": "/tmp"}
    tool_call_id = f"call_{uuid.uuid4().hex[:8]}"
    return [
        {"role": "user", "content": "Find python files in /tmp"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": function_name,
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": tool_result,
        },
    ]


class MockResponseClass:
    """A proper response class for testing (not MagicMock) so type(response)(content=...) works."""
    def __init__(self, content="", tool_calls=None, model="test-model", finish_reason="stop"):
        self.content = content
        self.model = model
        self.tool_calls = tool_calls or []
        self.finish_reason = finish_reason
        self.usage = None

def _make_mock_response(
    content: str = "",
    tool_calls: List[dict] = None,
    model: str = "test-model",
) -> MockResponseClass:
    """Create a mock response for filter testing."""
    return MockResponseClass(content=content, tool_calls=tool_calls, model=model)


def _make_context(
    conv: List[dict] = None,
    upstream_model: str = "local/deep",
    upstream_url: str = "http://test:1234/v1",
    stream: bool = False,
) -> FilterExecutionContext:
    """Create a test filter execution context."""
    ctx = FilterExecutionContext(
        req_id="test_req_id",
        upstream_payload={"messages": conv or []},
        route_name="test",
        upstream_model=upstream_model,
        upstream_url=upstream_url,
    )
    ctx.metadata["conversation_history"] = conv or []
    ctx.metadata["upstream_url"] = upstream_url
    ctx.metadata["upstream_model"] = upstream_model
    if stream:
        ctx.stream = True
    return ctx


# ── Detection Tests ───────────────────────────────────────────────────────────


class TestNonStreaming:
    @pytest.mark.asyncio
    async def test_no_tool_calls_passes_through(self):
        """Response without tool calls returns unchanged."""
        conv = _make_conv_with_tool("search_file")
        resp = _make_mock_response(content="No tool calls here")
        ctx = _make_context(conv)

        f = ToolLoopStopperFilter()
        result = await f.process_response(resp, ctx)
        assert result == resp or result.content == resp.content

    @pytest.mark.asyncio
    async def test_tool_call_no_loop_passes_through(self):
        """Different tool call than last one returns unchanged."""
        conv = _make_conv_with_tool("search_file", {"pattern": "*.py"})
        resp = _make_mock_response(
            content="",
            tool_calls=[
                {
                    "id": "new_call",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "/tmp/other.txt"}',
                    },
                }
            ],
        )
        ctx = _make_context(conv)
        f = ToolLoopStopperFilter()
        result = await f.process_response(resp, ctx)
        assert result is resp  # No loop = pass through

    @pytest.mark.asyncio
    async def test_loop_detected_strips_repeated_call(self):
        """Same tool call triggers TLS intervention."""
        conv = _make_conv_with_tool("search_file", {"pattern": "*.py"})
        resp = _make_mock_response(
            content="I'll search again",
            tool_calls=[
                {
                    "id": "repeated_call",
                    "type": "function",
                    "function": {
                        "name": "search_file",
                        "arguments": '{"pattern": "*.py"}',
                    },
                }
            ],
        )
        ctx = _make_context(conv)

        # Mock the HTTP retry to simulate upstream responding to TLS
        async def mock_retry(messages, model, upstream_url):
            assert len(messages) > len(conv)  # Conversation grew
            # Check TLS message was injected
            tool_msgs = [m for m in messages if m["role"] == "tool"]
            assert len(tool_msgs) >= 2  # Original tool + TLS
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Already searched that. Let's try reading the file at /tmp/test.py",
                            "tool_calls": [],
                        }
                    }
                ]
            }

        f = ToolLoopStopperFilter()
        f._make_http_retry = mock_retry
        result = await f.process_response(resp, ctx)

        assert result is not resp  # New response returned
        assert "Already searched" in result.content
        assert "Already searched" in result.content  # response_B is in output
        # The repeated tool call is gone from the response content

    @pytest.mark.asyncio
    async def test_fallback_when_model_repeats_again(self):
        """After TLS, if model repeats same tool call, return fallback."""
        conv = _make_conv_with_tool("search_file", {"pattern": "*.py"})
        resp = _make_mock_response(
            content="",
            tool_calls=[
                {
                    "id": "r1",
                    "type": "function",
                    "function": {"name": "search_file", "arguments": '{"pattern": "*.py"}'},
                }
            ],
        )
        ctx = _make_context(conv)

        async def mock_retry(messages, model, upstream_url):
            # Model repeats the SAME tool call after TLS
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "r2",
                                    "type": "function",
                                    "function": {"name": "search_file", "arguments": '{"pattern": "*.py"}'},
                                }
                            ],
                        }
                    }
                ]
            }

        f = ToolLoopStopperFilter()
        f._make_http_retry = mock_retry
        result = await f.process_response(resp, ctx)

        assert result is not resp
        assert "repeating" in result.content.lower() or "different" in result.content.lower()


# ── Configuration Tests ────────────────────────────────────────────────────────




# ── Streaming Tests ───────────────────────────────────────────────────────────


class TestStreaming:
    @pytest.mark.asyncio
    async def test_streaming_no_tool_calls_passes_through(self):
        """Streaming response without tool calls returns unchanged."""
        conv = _make_conv_with_tool("search_file")
        resp = _make_mock_response(content="Normal streaming response")
        ctx = _make_context(conv, stream=True)

        f = ToolLoopStopperFilter()
        result = await f.process_response(resp, ctx)
        assert result is resp  # Passed through unchanged

    @pytest.mark.asyncio
    async def test_streaming_different_tool_call_passes_through(self):
        """Streaming with different tool call than last one returns unchanged."""
        conv = _make_conv_with_tool("search_file", {"pattern": "*.py"})
        resp = _make_mock_response(
            content="Let me read a file",
            tool_calls=[
                {
                    "id": "new_tc",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "/tmp/test.py"}',
                    },
                }
            ],
        )
        ctx = _make_context(conv, stream=True)
        f = ToolLoopStopperFilter()
        result = await f.process_response(resp, ctx)
        assert result is resp

    @pytest.mark.asyncio
    async def test_streaming_loop_detected_replaces_content(self):
        """Streaming loop: filter replaces response with TLS result."""
        conv = _make_conv_with_tool("search_file", {"pattern": "*.py"})
        resp = _make_mock_response(
            content="I'll search again",
            tool_calls=[
                {
                    "id": "repeated",
                    "type": "function",
                    "function": {
                        "name": "search_file",
                        "arguments": '{"pattern": "*.py"}',
                    },
                }
            ],
        )
        ctx = _make_context(conv, stream=True)

        async def mock_retry(messages, model, upstream_url):
            return {
                "choices": [
                    {"message": {"content": "Already did that. Here's the file content.", "tool_calls": []}}
                ]
            }

        f = ToolLoopStopperFilter()
        f._make_http_retry = mock_retry
        result = await f.process_response(resp, ctx)
        assert result is not resp
        assert "Already did that" in result.content


class TestConfig:
    def test_default_config(self):
        """Default config has valid values."""
        config = ToolLoopStopperConfig()
        assert config.max_repeats == 1
        assert config.fuzzy_look_back == 0
        assert config.fuzzy_max_repeats == 0
        assert config.tls_message  # not empty
        assert config.fallback_message  # not empty

    def test_empty_tls_message_defaults(self):
        """Empty tls_message gets auto-defaulted."""
        config = ToolLoopStopperConfig()
        config.tls_message = ""
        config.__post_init__()
        assert config.tls_message == "You've already executed this tool call with the same arguments. Please proceed with the next step or try a different approach."

    def test_empty_fallback_message_defaults(self):
        """Empty fallback_message gets auto-defaulted."""
        config = ToolLoopStopperConfig()
        config.fallback_message = ""
        config.__post_init__()
        assert config.fallback_message == "I notice I'm repeating the same tool call. Let me try a different approach."

    def test_backward_compat_max_attempts(self):
        """'max_attempts' config key still works (mapped to max_repeats)."""
        f = ToolLoopStopperFilter(config={"max_attempts": 3})
        assert f.config.max_repeats == 3


# ── Helper Method Tests ───────────────────────────────────────────────────────


class TestHelperMethods:
    """Test the new refactored helper methods directly."""

    def test_get_signatures_from_conv(self):
        """Extract tool call signatures from conversation."""
        f = ToolLoopStopperFilter()
        conv = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}
            ]},
            {"role": "tool", "content": "sunny"},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "get_time", "arguments": '{"city":"Rome"}'}}
            ]},
        ]
        sigs = f._get_signatures_from_conv(conv)
        assert len(sigs) == 2
        assert sigs[0] == ("get_weather", json.dumps({"city": "Rome"}, sort_keys=True))
        assert sigs[1][0] == "get_time"

    def test_get_signatures_empty(self):
        """Empty conversation returns empty list."""
        f = ToolLoopStopperFilter()
        assert f._get_signatures_from_conv([]) == []

    def test_count_consecutive_from_end_basic(self):
        """Count consecutive matches from end."""
        f = ToolLoopStopperFilter()
        sigs = [("a","{}"), ("b","{}"), ("b","{}"), ("b","{}")]
        assert f._count_consecutive_from_end(sigs, ("b","{}")) == 3
        assert f._count_consecutive_from_end(sigs, ("a","{}")) == 0

    def test_count_consecutive_from_end_single(self):
        """Single consecutive match."""
        f = ToolLoopStopperFilter()
        sigs = [("a","{}"), ("b","{}"), ("c","{}")]
        assert f._count_consecutive_from_end(sigs, ("c","{}")) == 1
        assert f._count_consecutive_from_end(sigs, ("a","{}")) == 0

    def test_count_consecutive_from_end_break(self):
        """Counting stops at first non-match."""
        f = ToolLoopStopperFilter()
        # Last is "a", then "b" — so consecutive "a" = 1 (just the last)
        sigs = [("b","{}"), ("a","{}")]
        assert f._count_consecutive_from_end(sigs, ("a","{}")) == 1

    def test_count_fuzzy_in_window_nolimit(self):
        """fuzzy_look_back=0 scans all."""
        f = ToolLoopStopperFilter()
        sigs = [("a","{}"), ("b","{}"), ("a","{}"), ("c","{}"), ("a","{}")]
        assert f._count_fuzzy_in_window(sigs, ("a","{}"), look_back=0) == 3

    def test_count_fuzzy_in_window_limited(self):
        """fuzzy_look_back=N limits to last N."""
        f = ToolLoopStopperFilter()
        sigs = [("a","{}"), ("b","{}"), ("a","{}"), ("c","{}"), ("a","{}")]
        # Last 3: [("a","{}"), ("c","{}"), ("a","{}")] → 2 matches
        assert f._count_fuzzy_in_window(sigs, ("a","{}"), look_back=3) == 2
        # Last 1: [("a","{}")] → 1 match
        assert f._count_fuzzy_in_window(sigs, ("a","{}"), look_back=1) == 1


# ── New Detection Behavior Tests ─────────────────────────────────────────────


class TestConsecutiveRepeats:
    """Test max_repeats (consecutive) detection."""

    def make_resp(self, tc_dicts):
        """Build a minimal mock response with tool_calls."""
        from keeprollming.orchestrator.filter import StreamingResponse
        return StreamingResponse(content="", tool_calls=tc_dicts)

    def _make_ctx(self, conv, req_id="test", upstream_url="http://upstream"):
        """Build FilterExecutionContext with proper upstream info."""
        return FilterExecutionContext(
            req_id=req_id,
            upstream_payload={"messages": conv},
            upstream_url=upstream_url,
            upstream_model="test-model",
        )

    async def _mock_retry_ok(self, messages, model, upstream_url):
        """Mock HTTP retry async."""
        return {"choices": [{"message": {"content": "ok, moving on", "tool_calls": []}}]}

    def test_max_repeats_1_triggers_on_first_repeat(self):
        """max_repeats=1: fire on first repeat (current behavior)."""
        f = ToolLoopStopperFilter(config={"max_repeats": 1})
        f._make_http_retry = self._mock_retry_ok
        conv = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}
            ]},
            {"role": "tool", "content": "sunny"},
        ]
        ctx = self._make_ctx(conv)

        resp = self.make_resp([{
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}
        }])
        result = asyncio.run(f.process_response(resp, ctx))

        assert result is not resp  # Triggers: new response created

    def test_max_repeats_2_triggers_on_second_repeat(self):
        """max_repeats=2: fires after two identical calls in conv + current."""
        f = ToolLoopStopperFilter(config={"max_repeats": 2})
        f._make_http_retry = self._mock_retry_ok
        conv = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}
            ]},
            {"role": "tool", "content": "result1"},
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}
            ]},
            {"role": "tool", "content": "result2"},
        ]
        ctx = self._make_ctx(conv)

        resp = self.make_resp([{
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}
        }])
        result = asyncio.run(f.process_response(resp, ctx))

        assert result is not resp  # Enough repeats → triggers

    def test_max_repeats_2_needs_two_consecutive(self):
        """max_repeats=2: one repeat is NOT enough, two repeats needed."""
        f = ToolLoopStopperFilter(config={"max_repeats": 2})
        conv = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}
            ]},
            {"role": "tool", "content": "result"},
        ]
        ctx = self._make_ctx(conv)

        resp = self.make_resp([{
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}
        }])
        result = asyncio.run(f.process_response(resp, ctx))

        assert result is resp  # Not enough repeats → passes through

    def test_max_repeats_0_disabled(self):
        """max_repeats=0: no exact-match detection."""
        f = ToolLoopStopperFilter(config={"max_repeats": 0})
        conv = [
            {"role": "assistant", "tool_calls": [
                {"function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}
            ]},
            {"role": "tool", "content": "result"},
        ]
        ctx = self._make_ctx(conv)

        resp = self.make_resp([{
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}
        }])
        result = asyncio.run(f.process_response(resp, ctx))
        assert result is resp  # Passes through


class TestFuzzyLookBack:
    """Test fuzzy_look_back windowed counting."""

    def make_resp(self, tc_dicts):
        from keeprollming.orchestrator.filter import StreamingResponse
        return StreamingResponse(content="", tool_calls=tc_dicts)

    def make_conv(self, names_args):
        """Build conversation from list of (name, args) tool calls, interleaved with tool results."""
        conv = [{"role": "user", "content": "start"}]
        for i, (name, args) in enumerate(names_args):
            conv.append({"role": "assistant", "tool_calls": [
                {"function": {"name": name, "arguments": json.dumps(args)}}
            ]})
            conv.append({"role": "tool", "content": f"result{i}"})
        return conv

    def _make_ctx(self, conv, req_id="test", upstream_url="http://upstream"):
        return FilterExecutionContext(
            req_id=req_id,
            upstream_payload={"messages": conv},
            upstream_url=upstream_url,
            upstream_model="test-model",
        )

    async def _mock_retry_ok(self, messages, model, upstream_url):
        return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}

    def test_fuzzy_with_look_back_limits_window(self):
        """fuzzy_max_repeats=3 with look_back=3: only counts in last 3."""
        f = ToolLoopStopperFilter(config={
            "fuzzy_max_repeats": 3,
            "fuzzy_look_back": 3,
        })
        f._make_http_retry = self._mock_retry_ok
        conv = self.make_conv([
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Rome"}),
        ])
        ctx = self._make_ctx(conv)

        resp = self.make_resp([{
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}
        }])
        result = asyncio.run(f.process_response(resp, ctx))
        assert result is not resp

    def test_fuzzy_with_look_back_respects_boundary(self):
        """fuzzy_max_repeats=3 with look_back=3: 2 in window, extra outside → no trigger."""
        f = ToolLoopStopperFilter(config={
            "max_repeats": 0,  # Disable exact match
            "fuzzy_max_repeats": 3,
            "fuzzy_look_back": 3,
        })
        conv = self.make_conv([
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Milan"}),
            ("get_weather", {"city": "Rome"}),
        ])
        ctx = self._make_ctx(conv)

        resp = self.make_resp([{
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}
        }])
        result = asyncio.run(f.process_response(resp, ctx))
        assert result is resp

    def test_fuzzy_look_back_0_scans_all(self):
        """fuzzy_look_back=0 scans entire conversation (default)."""
        f = ToolLoopStopperFilter(config={
            "fuzzy_max_repeats": 3,
            "fuzzy_look_back": 0,
        })
        f._make_http_retry = self._mock_retry_ok
        conv = self.make_conv([
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Milan"}),
            ("get_weather", {"city": "Rome"}),
            ("get_weather", {"city": "Naples"}),
            ("get_weather", {"city": "Rome"}),
        ])
        ctx = self._make_ctx(conv)

        resp = self.make_resp([{
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}
        }])
        result = asyncio.run(f.process_response(resp, ctx))
        assert result is not resp


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
