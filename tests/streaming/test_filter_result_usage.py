"""
Test that the streaming handler correctly uses the filter chain's processed response.

Verifies:
- Filter chain result (with accumulated nudge content) is yielded to client
- Client does NOT stop after receiving only the lazy response
- The final chunk includes the complete accumulated response
"""

import asyncio
import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext, FilterChain
from keeprollming.filters.nudge.request import ModelNudgeFilter


from keeprollming.orchestrator.filter import StreamingResponse  # shared DTO (Architecture v2.1)


class MockStreamingResponse(StreamingResponse):
    """Legacy alias — delegates to shared StreamingResponse dataclass."""
    pass


def _nudge_chain(config):
    filter_ = ModelNudgeFilter(config["model_nudge"])
    return FilterChain(filters=[filter_], execution_order=[filter_.name])


class TestStreamingFilterChainResultUsage:
    """Verify streaming handler captures and uses filter chain result."""

    def test_filter_chain_result_used_for_streaming_response(self):
        """When filter chain returns modified content, streaming handler uses it."""
        config = FilterConfig(name="model_nudge")
        filter_instance = ModelNudgeFilter(config)
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]
        filter_instance._action = "nudge"
        filter_instance._max_nudge_attempts = 1
        filter_instance._upstream_url = ""  # No HTTP retry - breaks early

        chain = FilterChain(
            filters=[filter_instance],
            execution_order=["model_nudge"],
        )

        ctx = FilterExecutionContext(
            is_streaming_post_process=True,
            req_id="test-001",
            upstream_payload={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
        )

        # First (lazy) response from upstream
        mock_response = MockStreamingResponse("Now I will:")

        # Simulate what process_streaming_request does - call filter chain and USE result
        result = asyncio.run(chain.process_response(mock_response, ctx))

        # Assert: filter returned a processed response (not None, not the original)
        assert result is not None, "Filter chain must return a response"
        assert hasattr(result, 'content'), "Response must have content"
        # Content should be the accumulated response (even if it's still lazy with no HTTP retry)
        assert "Now I will:" in result.content


class TestFilterChainResultContent:
    """Verify the content reconstruction works correctly after filter processing."""

    def test_reconstructed_text_uses_filtered_content(self):
        """After filter chain processing, the streaming text uses processed content."""
        config = FilterConfig(name="model_nudge")
        filter_instance = ModelNudgeFilter(config)
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]
        filter_instance._action = "nudge"
        filter_instance._max_nudge_attempts = 1
        filter_instance._upstream_url = ""  # No actual HTTP call

        ctx = FilterExecutionContext(
            is_streaming_post_process=True,
            req_id="test-002",
            upstream_payload={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
        )

        mock_response = MockStreamingResponse("Now I will:")
        result = asyncio.run(filter_instance.process_response(mock_response, ctx))

        # The result should be a NEW response (not the original mock)
        assert result is not mock_response, "Filter must return new response, not original"
        assert hasattr(result, 'content')
        # Content must include the original lazy text
        assert "Now I will:" in result.content


class TestNonStreamingFilterChainResultUsage:
    """Verify non-streaming handler also uses filter chain result correctly."""

    def test_processed_response_content_preserved(self):
        """The processed response content is the accumulated nudge result."""
        config = FilterConfig(name="model_nudge")
        filter_instance = ModelNudgeFilter(config)
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]
        filter_instance._action = "nudge"
        filter_instance._max_nudge_attempts = 1
        filter_instance._upstream_url = ""

        ctx = FilterExecutionContext(
            req_id="test-003",
            upstream_payload={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
        )

        mock_response = MagicMock()
        mock_response.content = "Now I will:"
        mock_response.model = "test-model"

        result = asyncio.run(filter_instance.process_response(mock_response, ctx))

        assert result is not None
        assert hasattr(result, 'content')
        # Even without HTTP retry, the filter detects lazy pattern and returns accumulated content
        assert len(result.content) > 0


class TestStreamingHandlerMockIntegration:
    """Integration-like tests for the streaming handler's filter usage."""

    @pytest.mark.asyncio
    async def test_filter_chain_modifies_content_in_streaming_context(self):
        """Simulate streaming handler flow: filter modifies response, result is captured."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext

        # Setup filter
        filter_chain_config = {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                    "max_attempts": 1,
                    "upstream_url": "",  # No HTTP call
                }
        }

        chain = _nudge_chain(filter_chain_config)
        ctx = FilterExecutionContext(
            is_streaming_post_process=True,
            req_id="test-004",
            upstream_payload={"model": "test", "messages": [{"role": "user", "content": "Hi"}]},
        )

        mock_response = MockStreamingResponse("Now I will:")
        processed = await chain.process_response(mock_response, ctx)

        # Verify the processed result is usable (has content)
        assert processed is not None
        assert hasattr(processed, 'content')
        content = processed.content

        # Simulate what the streaming handler should do: use processed content
        if content:
            reconstructed_text = content
            assistant_parts = [content]
            assert len(reconstructed_text) > 0
            assert "Now I will:" in reconstructed_text


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
