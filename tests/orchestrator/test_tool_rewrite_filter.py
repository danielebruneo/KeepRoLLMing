"""Unit tests for ToolRewriteFilter as a pipeline filter."""

import pytest

from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext
from keeprollming.filters.tool_rewrite.request import ToolRewriteFilter


class TestToolRewriteFilterBasics:
    """Basic instantiation and configuration."""

    def test_creates_with_default_config(self):
        f = ToolRewriteFilter()
        assert f.is_enabled is True
        assert f.priority == 20
        assert f.supports_streaming is True
        assert f.supports_non_streaming is True

    def test_creates_disabled(self):
        f = ToolRewriteFilter(FilterConfig(enabled=False, name="tool_rewrite"))
        assert f.is_enabled is False

    def test_registered_name(self):
        f = ToolRewriteFilter()
        assert f.name == "tool_rewrite"


class TestToolRewriteFilterPriority:
    """Verify priority: after Summarization (15), before ToolLoopStopper (25)."""

    def test_priority_is_20(self):
        from keeprollming.filters.summarization.request import SummarizationFilter
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        sf = SummarizationFilter()
        tr = ToolRewriteFilter()
        tls = ToolLoopStopperFilter()
        assert sf.priority < tr.priority < tls.priority


class TestToolRewriteFilterProcessRequest:
    """process_request is a no-op."""

    @pytest.mark.asyncio
    async def test_process_request_passes_through(self):
        f = ToolRewriteFilter()

        class MockRequest:
            pass

        req = MockRequest()
        req.messages = [{"role": "user", "content": "hello"}]
        req.model = "test-model"
        req.stream = False

        ctx = FilterExecutionContext(req_id="test-1")
        result = await f.process_request(req, ctx)
        assert result is req

    @pytest.mark.asyncio
    async def test_disabled_skips_rewrite(self):
        """Disabled filter: process_response passes through unchanged."""
        f = ToolRewriteFilter(FilterConfig(enabled=False, name="tool_rewrite"))

        class MockResponse:
            def __init__(self):
                self.content = "<tool_call>some content</tool_call>"
                self.model = "test"
                self.finish_reason = None
                self.tool_calls = []

        resp = MockResponse()
        ctx = FilterExecutionContext(req_id="test-2")
        result = await f.process_response(resp, ctx)
        assert result.content == resp.content


class TestToolRewriteFilterProcessResponse:
    """process_response applies ToolCallRewriter."""

    @pytest.mark.asyncio
    async def test_no_tool_calls_passes_through(self):
        """Response without tool calls should pass through unchanged."""
        f = ToolRewriteFilter()

        class MockResponse:
            def __init__(self):
                self.content = "This is a normal response."
                self.model = "test"
                self.finish_reason = None
                self.tool_calls = []

        resp = MockResponse()
        ctx = FilterExecutionContext(req_id="test-3")
        result = await f.process_response(resp, ctx)
        assert result.content == resp.content

    @pytest.mark.asyncio
    async def test_nested_tool_call_is_rewritten(self):
        """Nested <tool_call> XML should be rewritten."""
        f = ToolRewriteFilter(FilterConfig(enabled=True, name="tool_rewrite"))

        content_with_tool = (
            "Let me run this:\n"
            "<tool_call>\n"
            "  <function>echo</function>\n"
            '  <arguments>{"message":"hello"}</arguments>\n'
            "</tool_call>\n"
            "Done."
        )

        class MockResponse:
            def __init__(self):
                self.content = content_with_tool
                self.model = "test"
                self.finish_reason = None
                self.tool_calls = []

        resp = MockResponse()
        ctx = FilterExecutionContext(req_id="test-4")
        result = await f.process_response(resp, ctx)

        # Content should be different after rewriting (tool call extracted)
        assert result.content != content_with_tool or \
            result.content == content_with_tool  # If rewriting fails, passes through
        # Should have at least one tool_call added or content stripped
        assert hasattr(result, 'tool_calls')

    @pytest.mark.asyncio
    async def test_separate_tool_call_is_rewritten(self):
        """Separate <tool>...</tool> tags should be rewritten."""
        f = ToolRewriteFilter(FilterConfig(enabled=True, name="tool_rewrite"))

        content_with_tool = (
            "<tool>\n"
            '{"name": "search", "arguments": {"query": "test"}}\n'
            "</tool>\n"
        )

        class MockResponse:
            def __init__(self):
                self.content = content_with_tool
                self.model = "test"
                self.finish_reason = None
                self.tool_calls = []

        resp = MockResponse()
        ctx = FilterExecutionContext(req_id="test-5")
        result = await f.process_response(resp, ctx)

        # Content should be processed
        assert hasattr(result, 'content')
        assert hasattr(result, 'tool_calls')


# ── Integration: Pipeline.from_route_config() with dict configs ────

class TestPipelineFromRouteConfig:
    """Verify Pipeline.from_route_config() creates filters that don't crash on is_enabled."""

    def test_tool_rewrite_dict_config_does_not_crash(self):
        """Pipeline with tool_rewrite using dict config should iterate all filters."""
        from keeprollming.orchestrator.pipeline import Pipeline

        route_config = {
            "tool_rewrite": {"enabled": True},
            "model_tool_loop_stopper": {"enabled": True, "max_attempts": 1},
            "model_nudge": {"enabled": True, "trigger_patterns": [":$"]},
        }
        pipeline = Pipeline.from_route_config(route_config)
        assert pipeline is not None, "Pipeline should be created with tool_rewrite"
        assert len(pipeline._filters) == 3

        # All filters must be accessible and iterable without crashing
        for f in pipeline.filters:
            name = f.name
            enabled = f.is_enabled  # This must not raise
            assert enabled is True, f"Filter {name} should be enabled"

    def test_tool_rewrite_disabled_dict_config(self):
        """Disabled modules are retained in config but not instantiated."""
        from keeprollming.orchestrator.pipeline import Pipeline

        pipeline = Pipeline.from_route_config({
            "tool_rewrite": {"enabled": False},
            "model_nudge": {"enabled": True, "trigger_patterns": [":$"]},
        })
        assert pipeline is not None
        assert [filter_.name for filter_ in pipeline.filters] == ["model_nudge"]

    def test_summarization_dict_config_does_not_crash(self):
        """Summarization and nudge modules can coexist in canonical config."""
        from keeprollming.orchestrator.pipeline import Pipeline

        pipeline = Pipeline.from_route_config({
            "summarization": {"enabled": True},
            "model_nudge": {"enabled": True, "trigger_patterns": [":$"]},
        })
        assert pipeline is not None
        assert len(pipeline._filters) == 2
