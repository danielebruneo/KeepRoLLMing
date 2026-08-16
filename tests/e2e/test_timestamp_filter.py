"""
Tests for TimestampFilter — injects request/response timestamps.

Tests cover:
- Request: system message injected at end of messages list
- Response: template appended to assistant content
- Response: no modification when no assistant content (tool_calls-only)
- Config: custom template and timezone
- Default behavior (UTC, default template)
- Pipeline integration
"""

import asyncio
import re
from unittest.mock import patch

import pytest

from keeprollming.orchestrator.filter import (
    FilterConfig,
    FilterExecutionContext,
)
from keeprollming.filters.timestamp.request import TimestampFilter


# ── Mock Request ──────────────────────────────────────────────────────

class MockRequest:
    def __init__(self, messages=None, model="test-model", stream=False):
        self.messages = messages or []
        self.model = model
        self.stream = stream


# ── Mock Response ─────────────────────────────────────────────────────

class MockResponse:
    def __init__(self, content="", model="test-model", finish_reason=None, tool_calls=None, usage=None, reasoning_content=""):
        self.content = content or ""
        self.model = model
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls
        self.usage = usage
        self.reasoning_content = reasoning_content or ""


# ── Helper: match default template footer ────────────────────────────

_DEFAULT_FOOTER_RE = re.compile(
    r"\n\n---\nTimestamp: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC"
)


# ── Default behavior tests ────────────────────────────────────────────

class TestTimestampFilterDefault:
    """Test default behavior: UTC timezone, default template."""

    def test_filter_declared_in_builtin_registry(self):
        """The explicit module registry is the only discovery mechanism."""
        from keeprollming.filters import built_in_filter_modules

        registry = built_in_filter_modules()
        assert registry["timestamp"].request_factory is TimestampFilter

    def test_default_priority(self):
        """Verify priority is 100 (runs last)."""
        assert TimestampFilter.priority == 100

    def test_default_name(self):
        """Verify default name is 'timestamp'."""
        assert TimestampFilter._default_name == "timestamp"

    def test_default_config(self):
        """Verify default config values."""
        f = TimestampFilter()
        assert f.is_enabled is True
        assert f._template == TimestampFilter._DEFAULT_TEMPLATE
        assert f._timezone == "UTC"
        assert f._bare_format == "%Y-%m-%d %H:%M:%S UTC"

    def test_process_request_injects_system_message(self):
        """Request phase: system message with timestamp is appended to messages."""
        f = TimestampFilter()
        req = MockRequest(messages=[{"role": "user", "content": "Hello"}])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        assert result is req
        assert len(req.messages) == 2
        assert req.messages[0] == {"role": "user", "content": "Hello"}
        assert req.messages[1]["role"] == "system"
        assert req.messages[1]["content"].startswith("Current UTC time:")
        # Timestamp stored in context
        assert "timestamp_str" in ctx.state

    def test_process_request_appends_after_all_messages(self):
        """Request phase: system message goes at the END of messages list."""
        f = TimestampFilter()
        req = MockRequest(messages=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "user", "content": "Second question"},
        ])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        assert len(req.messages) == 4
        assert req.messages[3]["role"] == "system"
        assert req.messages[3]["content"].startswith("Current UTC time:")

    def test_process_request_disabled_filter(self):
        """Request phase: disabled filter returns request unchanged."""
        f = TimestampFilter(config=FilterConfig(enabled=False))
        req = MockRequest(messages=[{"role": "user", "content": "Hello"}])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        assert len(result.messages) == 1
        assert "timestamp_str" not in ctx.state

    def test_process_response_appends_template(self):
        """Response phase: default template appended to assistant content."""
        f = TimestampFilter()
        ctx = FilterExecutionContext(req_id="test-req")

        response = MockResponse(content="Assistant response here")
        result = asyncio.run(f.process_response(response, ctx))

        assert result is not response  # New response object
        assert "Assistant response here" in result.content
        # Verify default template patterns
        assert _DEFAULT_FOOTER_RE.search(result.content)

    def test_process_response_no_content_unchanged(self):
        """Response phase: no content, no tool_calls → unchanged."""
        f = TimestampFilter()
        ctx = FilterExecutionContext(req_id="test-req")

        response = MockResponse(content="")
        result = asyncio.run(f.process_response(response, ctx))

        assert result is response  # Same object
        assert result.content == ""

    def test_process_response_tool_calls_only_unchanged(self):
        """Response phase: tool_calls-only → unchanged (no timestamp emitted)."""
        f = TimestampFilter()
        ctx = FilterExecutionContext(req_id="test-req")

        response = MockResponse(
            content="",
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "test"}}],
        )
        result = asyncio.run(f.process_response(response, ctx))

        assert result is response  # Same object, unchanged

    def test_process_response_preserves_reasoning_content(self):
        """Response phase: reasoning_content must be propagated to new response."""
        f = TimestampFilter()
        ctx = FilterExecutionContext(req_id="test-req")

        response = MockResponse(
            content="Assistant response here",
            reasoning_content="Let me think about this...",
        )
        result = asyncio.run(f.process_response(response, ctx))

        assert result is not response
        assert result.reasoning_content == "Let me think about this..."
        assert "Assistant response here" in result.content
        assert _DEFAULT_FOOTER_RE.search(result.content)


# ── Custom template/timezone tests ────────────────────────────────────

class TestTimestampFilterCustomConfig:
    """Test custom template and timezone configuration."""

    def test_custom_template(self):
        """Custom template string is used."""
        f = TimestampFilter(config={
            "template": "\n\n---\n[%H:%M:%S]",
        })
        ctx = FilterExecutionContext(req_id="test-req")
        response = MockResponse(content="Response")
        result = asyncio.run(f.process_response(response, ctx))

        assert re.search(r"\n\n---\n\[\d{2}:\d{2}:\d{2}\]", result.content)

    def test_custom_timezone(self):
        """Custom timezone is used."""
        f = TimestampFilter(config={
            "template": "\n\n---\n%Z",
            "timezone": "Europe/Rome",
        })
        ctx = FilterExecutionContext(req_id="test-req")
        response = MockResponse(content="Response")
        result = asyncio.run(f.process_response(response, ctx))

        # CEST or CET for Europe/Rome
        assert re.search(r"(CEST|CET)", result.content)

    def test_custom_emoji_template(self):
        """Template with emoji and different timezone."""
        f = TimestampFilter(config={
            "template": "\n\n---\n🕐 %d/%m/%Y %H:%M",
            "timezone": "Europe/Rome",
        })
        ctx = FilterExecutionContext(req_id="test-req")
        response = MockResponse(content="Response")
        result = asyncio.run(f.process_response(response, ctx))

        assert "🕐" in result.content
        assert re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", result.content)

    def test_bare_format_extracted(self):
        """_bare_format is correctly extracted from template."""
        f = TimestampFilter(config={
            "template": "\n\n---\nUTC: %Y-%m-%d %H:%M:%S",
        })
        assert f._bare_format == "%Y-%m-%d %H:%M:%S"

    def test_bare_format_used_for_request(self):
        """Request system message uses _bare_format timestamp."""
        f = TimestampFilter()
        req = MockRequest(messages=[{"role": "user", "content": "Hi"}])
        ctx = FilterExecutionContext(req_id="test-req")
        asyncio.run(f.process_request(req, ctx))

        system_msg = req.messages[-1]["content"]
        # Should be in format: "Current UTC time: 2026-06-02 12:00:00 UTC"
        assert re.search(r"Current UTC time: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", system_msg)

    def test_disabled_filter_does_nothing(self):
        """Disabled filter skips both request and response processing."""
        f = TimestampFilter(config=FilterConfig(enabled=False))
        req = MockRequest(messages=[{"role": "user", "content": "Hello"}])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))
        assert len(result.messages) == 1

        response = MockResponse(content="Response")
        result = asyncio.run(f.process_response(response, ctx))
        assert result is response

 # ── Pipeline integration tests ────────────────────────────────────────

class TestTimestampFilterPipeline:
    """Test TimestampFilter integration with Pipeline."""

    def test_pipeline_instantiates_timestamp_filter(self):
        """Pipeline.from_route_config creates TimestampFilter for 'timestamp' name."""
        from keeprollming.orchestrator.pipeline import Pipeline

        route_config = {
            "timestamp": {
                "enabled": True,
                "template": "\n\n---\nTest: %Y-%m-%d %H:%M:%S UTC",
                "timezone": "UTC",
            },
        }

        pipeline = Pipeline.from_route_config(route_config)
        assert pipeline is not None
        assert len(pipeline.filters) == 1
        assert isinstance(pipeline.filters[0], TimestampFilter)
        assert pipeline.filters[0].is_enabled is True
        assert pipeline.filters[0]._template == "\n\n---\nTest: %Y-%m-%d %H:%M:%S UTC"

    def test_pipeline_timestamp_runs_last(self):
        """Timestamp filter has highest priority (runs last)."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.filters.system_prompt.request import SystemPromptFilter

        route_config = {
            "system_prompt": {"enabled": True, "prompt": "You are helpful."},
            "timestamp": {"enabled": True},
        }

        pipeline = Pipeline.from_route_config(route_config)
        assert pipeline is not None
        assert pipeline.filters[0].priority < pipeline.filters[1].priority
        assert isinstance(pipeline.filters[0], SystemPromptFilter)
        assert isinstance(pipeline.filters[1], TimestampFilter)


# ── Edge case tests ───────────────────────────────────────────────────

class TestTimestampFilterEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_messages_list(self):
        """Request with empty messages list: system message is still added."""
        f = TimestampFilter()
        req = MockRequest(messages=[])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        assert len(result.messages) == 1
        assert result.messages[0]["role"] == "system"

    def test_existing_system_message(self):
        """Request with existing system message: new timestamp system message is appended."""
        f = TimestampFilter()
        req = MockRequest(messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        assert len(result.messages) == 3
        assert result.messages[0]["role"] == "system"
        assert result.messages[0]["content"] == "You are a helpful assistant."
        assert result.messages[1]["role"] == "user"
        assert result.messages[2]["role"] == "system"
        assert result.messages[2]["content"].startswith("Current UTC time:")

    def test_response_with_newlines_in_content(self):
        """Response with newlines in content: footer is properly added."""
        f = TimestampFilter()
        ctx = FilterExecutionContext(req_id="test-req")

        response = MockResponse(content="Line 1\nLine 2\nLine 3")
        result = asyncio.run(f.process_response(response, ctx))

        assert "Line 1\nLine 2\nLine 3" in result.content
        assert _DEFAULT_FOOTER_RE.search(result.content)

    def test_reset_clears_state(self):
        """reset() method exists and doesn't raise."""
        f = TimestampFilter()
        f.reset()

    def test_request_updates_existing_timestamp_in_place(self):
        """Request phase: existing 'Current UTC time:' system message is updated in place."""
        f = TimestampFilter()
        req = MockRequest(messages=[
            {"role": "system", "content": "Current UTC time: 2026-01-01 00:00:00 UTC"},
            {"role": "user", "content": "Hello"},
        ])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        # Still 2 messages (no append), first updated
        assert len(result.messages) == 2
        assert result.messages[0]["role"] == "system"
        assert result.messages[0]["content"] != "Current UTC time: 2026-01-01 00:00:00 UTC"
        assert result.messages[0]["content"].startswith("Current UTC time:")
        assert result.messages[1]["role"] == "user"

    def test_request_does_not_update_non_timestamp_system_message(self):
        """Request phase: non-timestamp system messages are not updated."""
        f = TimestampFilter()
        req = MockRequest(messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        # 3 messages — original system + user + new timestamp
        assert len(result.messages) == 3
        assert result.messages[0]["content"] == "You are a helpful assistant."
        assert result.messages[2]["role"] == "system"
        assert result.messages[2]["content"].startswith("Current UTC time:")

    def test_request_updates_first_timestamp_skips_later_ones(self):
        """Request phase: only the first 'Current UTC time:' system message is updated."""
        f = TimestampFilter()
        # Two timestamp system messages (shouldn't happen but handle it)
        req = MockRequest(messages=[
            {"role": "system", "content": "Current UTC time: 2026-01-01 00:00:00 UTC"},
            {"role": "system", "content": "Current UTC time: 2026-01-02 00:00:00 UTC"},
            {"role": "user", "content": "Hello"},
        ])
        ctx = FilterExecutionContext(req_id="test-req")

        result = asyncio.run(f.process_request(req, ctx))

        # Still 3 messages — first updated, second unchanged
        assert len(result.messages) == 3
        assert result.messages[0]["content"].startswith("Current UTC time:")
        assert "2026-01-01" not in result.messages[0]["content"]
        assert result.messages[1]["content"] == "Current UTC time: 2026-01-02 00:00:00 UTC"

    def test_response_replaces_stale_timestamp_footer(self):
        """Response phase: replace stale timestamp footer with a fresh one."""
        f = TimestampFilter()
        ctx = FilterExecutionContext(req_id="test-req")

        # Content that already ends with the default template pattern
        existing_footer = "\n\n---\nTimestamp: 2026-06-05 16:29:27 UTC"
        response = MockResponse(content=f"Some text here{existing_footer}")
        result = asyncio.run(f.process_response(response, ctx))

        # Should be a DIFFERENT object (replaced, not skipped)
        assert result is not response
        assert result.content is not None
        # Content should have the fresh template footer
        assert "Some text here" in result.content
        assert "Timestamp:" in result.content
        # Stale timestamp should be gone
        assert "2026-06-05" not in result.content

    def test_response_does_not_skip_when_content_has_different_pattern(self):
        """Response phase: append normally when footer pattern doesn't match."""
        f = TimestampFilter()
        ctx = FilterExecutionContext(req_id="test-req")

        # Content that looks different — no timestamp footer
        response = MockResponse(content="Some text with --- separator for other reasons")
        result = asyncio.run(f.process_response(response, ctx))

        assert result is not response
        assert _DEFAULT_FOOTER_RE.search(result.content)
        assert "Some text with --- separator" in result.content
