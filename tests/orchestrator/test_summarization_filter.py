"""Unit tests for SummarizationFilter as a pipeline filter."""

import pytest

from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext
from keeprollming.filters.summarization.request import SummarizationFilter


class TestSummarizationFilterBasics:
    """Basic instantiation and configuration."""

    def test_creates_with_default_config(self):
        f = SummarizationFilter()
        assert f.is_enabled is True
        assert f.priority == 15
        assert f.supports_streaming is True
        assert f.supports_non_streaming is True

    def test_creates_disabled(self):
        f = SummarizationFilter(FilterConfig(enabled=False, name="summarization"))
        assert f.is_enabled is False

    def test_registered_name(self):
        f = SummarizationFilter()
        assert f.name == "summarization"


class TestSummarizationFilterPriority:
    """Verify priority ordering: after SystemPrompt (10), before ToolRewrite (20)."""

    def test_priority_is_15(self):
        from keeprollming.filters.system_prompt.request import SystemPromptFilter
        from keeprollming.filters.tool_rewrite.request import ToolRewriteFilter
        sp = SystemPromptFilter()
        sf = SummarizationFilter()
        tr = ToolRewriteFilter()
        assert sp.priority < sf.priority < tr.priority


class TestSummarizationFilterProcessRequest:
    """SummarizationFilter.process_request delegations."""

    @pytest.mark.asyncio
    async def test_passthrough_enabled_skips_summarization(self):
        """When passthrough is enabled, summarization is skipped."""
        f = SummarizationFilter()

        class MockRequest:
            pass

        req = MockRequest()
        req.messages = [{"role": "user", "content": "hello"}]
        req.model = "test-model"
        req.stream = False

        ctx = FilterExecutionContext(req_id="test-1")
        ctx.metadata["passthrough_enabled"] = True

        result = await f.process_request(req, ctx)
        assert result.messages == req.messages
        assert ctx.metadata.get("did_summarize") is not True

    @pytest.mark.asyncio
    async def test_disabled_filter_passes_through(self):
        """Disabled filter should not modify messages."""
        f = SummarizationFilter(FilterConfig(enabled=False, name="summarization"))

        class MockRequest:
            pass

        req = MockRequest()
        req.messages = [{"role": "user", "content": "hello"}]
        req.model = "test-model"
        req.stream = False

        ctx = FilterExecutionContext(req_id="test-2")

        result = await f.process_request(req, ctx)
        assert result.messages == req.messages

    @pytest.mark.asyncio
    async def test_process_response_is_noop(self):
        """process_response should pass through unchanged."""
        f = SummarizationFilter()

        class MockResponse:
            def __init__(self):
                self.content = "test content"
                self.model = "test"
                self.finish_reason = None
                self.tool_calls = []

        resp = MockResponse()
        ctx = FilterExecutionContext(req_id="test-3")

        result = await f.process_response(resp, ctx)
        assert result is resp
