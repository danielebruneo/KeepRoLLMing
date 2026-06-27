"""
Comprehensive end-to-end test for Model Nudge filter with HTTP integration.

This test verifies the complete flow:
1. Filter detects lazy response pattern (e.g., ends with ":")
2. Filter handles retry INTERNALLY via process_response()
3. Returns Response objects directly (never raises StopFilterChain)
4. Makes HTTP calls to self._upstream_url for retries
5. If no upstream URL, breaks retry loop and returns accumulated content

The architecture changed: process_response() now encapsulates ALL retry logic.
"""

import json

import pytest
import asyncio
from httpx import AsyncClient, Response as HTTPXResponse
from fastapi.testclient import TestClient


# ============================================================================
# Helper: MockResponse with proper __init__ (not class-level attributes)
# ============================================================================

class MockResponse:
    """Mock response that accepts keyword arguments like the real Response."""
    def __init__(self, content=None, model=None, finish_reason=None):
        self.content = content
        self.model = model
        self.finish_reason = finish_reason


# ============================================================================
# Tests
# ============================================================================

class TestNudgeFilterFullFlow:
    """Test the complete nudge filter flow - filter handles retry INTERNALLY."""

    def test_nudge_filter_triggers_on_lazy_response(self):
        """
        Test that nudge filter detects lazy response and returns Response directly.

        Since no upstream URL is configured, the filter:
        1. Detects lazy pattern (content ending with ":")
        2. Enters retry loop
        3. Sees no upstream URL configured, logs warning, breaks
        4. Returns accumulated content (original content, since no retry occurred)

        No StopFilterChain is ever raised - filter handles everything internally.
        """
        from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterExecutionContext

        # Configure filter with no upstream URL (unit test mode)
        filter_config = {
            "enabled": True,
            "trigger_patterns": [":$"],
            "action": "nudge",
            "nudge_message": "Please complete your response.",
            "max_nudge_attempts": 3,
            "upstream_url": None,  # No upstream for unit test
        }

        filt = ModelNudgeFilter(filter_config)

        mock_resp = MockResponse(
            content="Here is the answer:",
            model="test-model",
            finish_reason=None,
        )
        context = FilterExecutionContext(req_id="test-001")

        # Should NOT raise StopFilterChain - filter handles retry internally
        result = asyncio.run(filt.process_response(mock_resp, context))

        # Filter returns a Response with accumulated content
        assert result is not None
        assert result.content == "Here is the answer:"
        # Original response is not modified (no retry happened, so accumulator == original)

    def test_filter_chain_execution_logging(self):
        """Test that filter chain passes through complete responses unchanged."""
        from keeprollming.orchestrator.filter import (
            FilterChain,
            FilterExecutionContext
        )

        # Configure filter chain
        filter_chain_config = {
            "order": ["model_nudge"],
            "filters": {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                }
            },
        }

        chain = FilterChain.from_route_config(filter_chain_config)

        mock_resp = MockResponse(
            content="Complete response.",
            model="test-model",
            finish_reason="stop",
        )
        context = FilterExecutionContext(req_id="test-002")

        # Process response (should NOT trigger filter - complete response, no ":" at end)
        result = asyncio.run(chain.process_response(mock_resp, context))

        # Response returned as-is (no lazy pattern detected)
        assert result is mock_resp
        assert context.metadata.get("nudge_attempts") == 0


class TestNudgeFilterWithMockHTTP:
    """Test nudge filter retry loop with mocked HTTP calls."""

    def test_complete_nudge_retry_flow(self):
        """
        Test that filter handles retry internally and returns Response directly.

        Filter detects lazy response, enters retry loop, but since no upstream
        URL is configured, it breaks and returns accumulated content.
        No StopFilterChain is raised.
        """
        from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterExecutionContext

        # Configure filter with no upstream URL
        filter_config = {
            "enabled": True,
            "trigger_patterns": [":$"],
            "action": "nudge",
            "nudge_message": "Please complete.",
            "max_nudge_attempts": 3,
            "upstream_url": None,
        }

        filt = ModelNudgeFilter(filter_config)

        mock_resp = MockResponse(
            content="The answer is:",
            model="test-model",
            finish_reason=None,
        )
        context = FilterExecutionContext(req_id="test-003")

        # Filter must NOT raise StopFilterChain - it handles retry internally
        result = asyncio.run(filt.process_response(mock_resp, context))

        assert result is not None
        assert result.content == "The answer is:"

    def test_max_nudge_attempts_prevents_infinite_loop(self, monkeypatch):
        """
        Test that max_nudge_attempts limits the retry loop internally.

        When the upstream always returns lazy responses, the filter should
        stop after max_nudge_attempts iterations and return accumulated content.
        No StopFilterChain is ever raised.
        """
        from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterExecutionContext

        # Configure filter with max 2 attempts and a truthy upstream_url
        # so the loop actually iterates (instead of breaking immediately)
        filter_config = {
            "enabled": True,
            "trigger_patterns": [":$"],
            "action": "nudge",
            "nudge_message": "Continue.",
            "max_nudge_attempts": 2,
            "upstream_url": "http://mock-upstream:8000",  # Truthy to avoid early break
        }

        filt = ModelNudgeFilter(filter_config)

        # Mock _make_http_retry to always return lazy content
        async def mock_http_retry(self, messages, model, upstream_url, **kwargs):
            return {"choices": [{"message": {"content": "Still explaining:"}}]}

        monkeypatch.setattr(ModelNudgeFilter, '_make_http_retry', mock_http_retry)

        mock_resp = MockResponse(
            content="Still explaining:",
            model="test-model",
            finish_reason=None,
        )
        context = FilterExecutionContext(req_id="test-004")

        # Filter handles retry internally - must NOT raise StopFilterChain
        result = asyncio.run(filt.process_response(mock_resp, context))

        # Should return accumulated content from all attempts
        assert result is not None
        # 1 original + 2 retries = 3 copies of "Still explaining:" joined by newlines
        assert "Still explaining:" in result.content
        assert result.content.count("Still explaining:") == 3

    def test_retry_length_finish_reason_not_leaked(self, monkeypatch):
        """
        Regression: a non-streaming retry that returns finish_reason="length"
        (e.g. the retry reused the clamped max_tokens) must NOT leak that reason
        to the client. The nudge delivered a COMPLETE accumulated response, so the
        terminal reason must be "stop" — otherwise the client shows a spurious
        "Response truncated due to token limits." notice on sensible content.
        """
        from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterExecutionContext

        filter_config = {
            "enabled": True,
            "trigger_patterns": [":$"],
            "action": "nudge",
            "nudge_message": "Continue.",
            "max_nudge_attempts": 3,
            "upstream_url": "http://mock-upstream:8000",
        }
        filt = ModelNudgeFilter(filter_config)

        # Retry completes the answer (NOT lazy) but upstream reports "length"
        async def mock_http_retry(self, messages, model, upstream_url, **kwargs):
            return {"choices": [{
                "message": {"content": "Here is the full, complete answer."},
                "finish_reason": "length",
            }]}

        monkeypatch.setattr(ModelNudgeFilter, '_make_http_retry', mock_http_retry)

        mock_resp = MockResponse(content="The answer is:", model="test-model",
                                 finish_reason="length")
        context = FilterExecutionContext(req_id="test-fr-length")
        result = asyncio.run(filt.process_response(mock_resp, context))

        assert result is not None
        assert "Here is the full, complete answer." in result.content
        assert result.finish_reason == "stop", \
            f"Retry 'length' leaked to client! finish_reason={result.finish_reason!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
