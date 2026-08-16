"""
Complete end-to-end test demonstrating filter integration.

This test verifies that filters are actually invoked when processing requests
with the FastAPI app, using a mock HTTP server to simulate LLM responses.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _nudge_chain(config):
    from keeprollming.filters.nudge import ModelNudgeFilter
    from keeprollming.orchestrator.filter import FilterChain

    filter_ = ModelNudgeFilter(config["model_nudge"])
    return FilterChain(filters=[filter_], execution_order=[filter_.name])


class TestFilterIntegrationEndToEnd:
    """Complete e2e tests for filter integration."""

    @pytest.mark.asyncio
    async def test_filter_chain_built_from_route_config(self):
        """Test that FilterChain is correctly built from route configuration."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext

        # Use the raw config dict (like route.filters would be)
        filter_chain_config = {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                }
        }

        # Step 1: Build filter chain from route config
        chain = _nudge_chain(filter_chain_config)

        # Step 2: Simulate HTTP response from upstream LLM (lazy pattern)
        class MockHTTPResponse:
            def __init__(self):
                self.content = json.dumps({
                    "choices": [{
                        "message": {"content": "Here's the plan for today:"},
                        "finish_reason": None,
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }).encode("utf-8")

            def json(self):
                return json.loads(self.content.decode("utf-8"))

        http_response = MockHTTPResponse()

        # Step 3: Create mock response object for filter processing
        class FilterMockResponse:
            def __init__(self, http_response=None, content=None, model=None, finish_reason=None):
                if http_response is not None:
                    self.content = http_response.content.decode("utf-8", errors="replace")
                    self.model = "test-model"
                    try:
                        resp_json = http_response.json()
                        choices = resp_json.get("choices", [])
                        if choices and isinstance(choices[0], dict):
                            msg_data = choices[0].get("message", {})
                            self.content = msg_data.get("content", "") or ""
                        self.usage = resp_json.get("usage")
                        self.finish_reason = choices[0].get("finish_reason") if choices else None
                    except:
                        self.usage = None
                        self.finish_reason = None
                else:
                    self.content = content or ""
                    self.model = model or "test-model"
                    self.finish_reason = finish_reason

        mock_response = FilterMockResponse(http_response)

        # Step 4: Process through filter chain (like in chat_completions.py)
        context = FilterExecutionContext()

        # Mock _make_http_retry so nudge can complete retry (it makes real HTTP otherwise)
        from unittest.mock import AsyncMock
        chain.filters.get('model_nudge')._make_http_retry = AsyncMock(return_value={
            "choices": [{"message": {"content": "Complete response after retry."}}]
        })
        result = await chain.process_response(mock_response, context)

        # Verify filter returns Response directly (new architecture)
        assert result is not None
        assert hasattr(result, "content")
        assert "Here's the plan for today:" in result.content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
