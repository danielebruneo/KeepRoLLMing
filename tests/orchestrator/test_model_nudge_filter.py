"""Tests for Model Nudge filter."""

import re
from unittest.mock import MagicMock

import pytest

from keeprollming.orchestrator.filter import (
    FilterConfig,
    FilterExecutionContext,
)
from keeprollming.filters.nudge.request import ModelNudgeFilter


class MockResponse:
    """Mock response object for testing."""

    def __init__(self, content="", model="test-model", finish_reason=None, tool_calls=None):
        self.content = content
        self.model = model
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls


class TestModelNudgeFilter:
    """Tests for Model Nudge filter basic functionality."""

    def test_filter_creation_with_config(self):
        """Test creating filter with custom configuration."""
        config = FilterConfig(
            enabled=True,
            name="model_nudge",
        )
        
        # Manually set attributes since FilterConfig doesn't support them directly
        filter_instance = ModelNudgeFilter(config)
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]
        filter_instance._action = "nudge"
        filter_instance._nudge_message = "Keep going!"
        filter_instance._max_nudge_attempts = 5

        assert len(filter_instance._trigger_patterns) == 1
        assert filter_instance._action == "nudge"
        assert filter_instance._nudge_message == "Keep going!"
        assert filter_instance._max_nudge_attempts == 5

    def test_invalid_pattern_raises_error(self):
        """Test that invalid regex patterns raise ValueError."""
        config = FilterConfig(name="model_nudge")
        
        # Manually trigger pattern initialization with invalid pattern
        filter_instance = ModelNudgeFilter(config)
        try:
            filter_instance._init_patterns(["[invalid"])
            assert False, "Should have raised ValueError"
        except ValueError as e:
            # Error message contains both the pattern and Python's regex error
            assert "Invalid regex pattern" in str(e)

    def test_pattern_matching(self):
        """Test regex pattern matching for lazy responses."""
        config = FilterConfig(name="model_nudge")
        filter_instance = ModelNudgeFilter(config)
        
        # Add a pattern that matches text ending with colon
        filter_instance._init_patterns([r":$"])

        assert filter_instance._matches_lazy_response("Now I will:") is True
        assert filter_instance._matches_lazy_response("Testing:") is True
        assert filter_instance._matches_lazy_response("Normal response") is False


class TestNudgeAction:
    """Tests for nudge action behavior (new architecture: filter returns Response directly)."""

    def test_nudge_returns_response_not_exception(self):
        """Test that nudge filter returns a Response (never raises StopFilterChain)."""
        config = FilterConfig(name="model_nudge")
        filter_instance = ModelNudgeFilter(config)

        # Set up patterns to match
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]
        filter_instance._action = "nudge"
        filter_instance._max_nudge_attempts = 3
        filter_instance._upstream_url = ""  # No upstream = breaks retry loop early

        import asyncio

        context = FilterExecutionContext()
        response = MockResponse(content="Now I will:")

        result = asyncio.run(filter_instance.process_response(response, context))

        # Filter returns a Response object directly (not StopFilterChain)
        assert result is not None
        assert hasattr(result, "content")
        assert "Now I will:" in result.content

    def test_max_attempts_limit_returns_response(self):
        """Test that max nudge attempts returns a response (not exception)."""
        config = FilterConfig(name="model_nudge")
        filter_instance = ModelNudgeFilter(config)

        # Set up patterns to match
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]
        filter_instance._action = "nudge"
        filter_instance._max_nudge_attempts = 2
        filter_instance._upstream_url = ""  # No upstream = breaks retry loop early

        import asyncio

        context = FilterExecutionContext()
        response = MockResponse(content="Test:")

        # Each call returns a Response (never raises)
        for _ in range(5):
            result = asyncio.run(filter_instance.process_response(response, context))
            assert result is not None
            assert hasattr(result, "content")


class TestRegenerateAction:
    """Tests for regenerate action behavior (new architecture: filter returns Response directly)."""

    def test_regenerate_returns_response(self):
        """Test that regenerate filter returns a Response (never raises StopFilterChain)."""
        config = FilterConfig(name="model_nudge")
        filter_instance = ModelNudgeFilter(config)

        # Set up patterns and action
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]
        filter_instance._action = "regenerate"
        filter_instance._max_nudge_attempts = 3
        filter_instance._upstream_url = ""  # No upstream = breaks retry loop early

        import asyncio

        context = FilterExecutionContext()
        response = MockResponse(content="Now I will:")

        result = asyncio.run(filter_instance.process_response(response, context))

        # Filter returns a Response object directly (not StopFilterChain)
        assert result is not None
        assert hasattr(result, "content")
        assert "Now I will:" in result.content


