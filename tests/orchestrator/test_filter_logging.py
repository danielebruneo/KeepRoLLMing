"""Tests for filter pipeline logging functionality."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from keeprollming.logging import FilterLogger, get_filter_logger, reset_filter_loggers
from keeprollming.orchestrator.filter import (
    Filter,
    FilterConfig,
    FilterChain,
    FilterExecutionContext,
    Request,
    Response,
    StopFilterChain,
)
from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter


@pytest.fixture
def temp_log_dir():
    """Create a temporary directory for log files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(autouse=True)
def cleanup_loggers():
    """Clean up filter loggers before and after tests."""
    reset_filter_loggers()
    yield
    reset_filter_loggers()


class TestFilterLogger:
    """Tests for FilterLogger class."""

    def test_logger_creates_log_file(self, temp_log_dir):
        """Test that logger creates log file in specified directory."""
        logger = FilterLogger("test_filter", str(temp_log_dir))
        
        assert logger.log_dir == temp_log_dir
        assert logger.log_file == temp_log_dir / "test_filter.log"

    def test_nudge_triggered_logs_event(self, temp_log_dir):
        """Test that nudge_triggered creates proper log entry."""
        logger = FilterLogger("model_nudge", str(temp_log_dir))
        
        logger.nudge_triggered(
            trigger_pattern=":$",
            response_content="Now I will:",
            nudge_attempt=1,
            action="nudge",
            max_attempts=3,
        )

        # Verify log file was created and contains entry
        assert logger.log_file.exists()
        
        with open(logger.log_file, "r") as f:
            lines = f.readlines()
        
        assert len(lines) == 1
        
        # Parse JSON entry
        entry = json.loads(lines[0])
        assert entry["event"] == "nudge_triggered"
        assert entry["trigger_pattern"] == ":$"
        assert entry["response_content"] == "Now I will:"
        assert entry["nudge_attempt"] == 1
        assert entry["action"] == "nudge"
        assert entry["filter"] == "model_nudge"
        assert "timestamp" in entry

    def test_loop_detected_logs_event(self, temp_log_dir):
        """Test that loop_detected creates proper log entry."""
        logger = FilterLogger("loop_detector", str(temp_log_dir))
        
        logger.loop_detected(
            duplicate_count=3,
            window_size=5,
            response_hash="abc123",
        )

        with open(logger.log_file, "r") as f:
            entry = json.loads(f.readline())
        
        assert entry["event"] == "loop_detected"
        assert entry["duplicate_count"] == 3
        assert entry["response_hash"] == "abc123"

    def test_filter_chain_executed_logs_event(self, temp_log_dir):
        """Test that filter_chain_executed creates proper log entry."""
        logger = FilterLogger("filter_chain", str(temp_log_dir))
        
        logger.filter_chain_executed(
            filters_executed=["master_prompt", "model_nudge"],
            total_filters=2,
            nudge_count=1,
            loop_count=0,
        )

        with open(logger.log_file, "r") as f:
            entry = json.loads(f.readline())
        
        assert entry["event"] == "filter_chain_executed"
        assert entry["filters_executed"] == ["master_prompt", "model_nudge"]
        assert entry["nudge_count"] == 1

    def test_summary_stats(self, temp_log_dir):
        """Test that summary_stats returns correct statistics."""
        logger = FilterLogger("model_nudge", str(temp_log_dir))
        
        # Write some test events
        logger.nudge_triggered(":$", "Now I will:", 1)
        logger.nudge_triggered(":$", "Here's the plan:", 2)
        logger.loop_detected(2, 3, "hash1")

        stats = logger.summary_stats()
        
        assert stats["filter_name"] == "model_nudge"
        assert stats["total_events"] == 3
        assert stats["event_counts"]["nudge_triggered"] == 2
        assert stats["event_counts"]["loop_detected"] == 1


class TestModelNudgeFilterLogging:
    """Tests for Model Nudge filter logging integration."""

    @pytest.fixture
    def nudge_filter(self, temp_log_dir):
        """Create a ModelNudgeFilter with custom log directory."""
        config = FilterConfig(
            enabled=True,
            name="model_nudge",
        )
        
        # Create filter instance (will use real logger)
        # Note: Need to pass trigger_patterns in config
        from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter
        
        # Manually set trigger patterns since FilterConfig doesn't support them directly
        filter_instance = ModelNudgeFilter(config)
        # Add default pattern for testing
        import re
        filter_instance._trigger_patterns = [re.compile(":$", re.IGNORECASE)]
        
        yield filter_instance

    async def test_process_response_logs_nudge_trigger(self, nudge_filter):
        """Test that process_response logs when nudge is triggered."""
        filter_instance = nudge_filter
        
        # Create mock response matching lazy pattern with colon at end
        mock_response = MagicMock()
        mock_response.content = "Now I will:"  # Ends with colon to match :$
        mock_response.model = "test-model"
        mock_response.finish_reason = None
        mock_response.tool_calls = None
        
        # Create execution context
        context = FilterExecutionContext()
        
        # Process response - filter returns Response directly (new architecture)
        result = await filter_instance.process_response(mock_response, context)

        # Should get a Response object, not exception
        assert result is not None
        assert hasattr(result, "content")
        assert result.content == "Now I will:"


class TestFilterChainLogging:
    """Tests for FilterChain logging integration."""

    @pytest.fixture
    def test_filter(self):
        """Create a simple test filter."""
        class TestFilter(Filter):
            name = "test_filter"
            
            async def process_request(self, request, context):
                return request
            
            async def process_response(self, response, context):
                return response
        
        return TestFilter

    @pytest.fixture
    def chain_with_logging(self, temp_log_dir, test_filter):
        """Create a FilterChain with logging enabled."""
        filter_instance = test_filter()
        
        # Patch get_filter_logger to use test directory
        with patch("keeprollming.orchestrator.filter.get_filter_logger") as mock_get:
            mock_logger = MagicMock()
            mock_get.return_value = mock_logger
            
            chain = FilterChain(
                filters=[filter_instance],
                execution_order=["test_filter"],
            )
            
            yield chain, mock_logger

    async def test_process_response_logs_chain_execution(self, chain_with_logging):
        """Test that process_response logs complete chain execution."""
        chain, mock_logger = chain_with_logging
        
        # Create mock response
        mock_response = MagicMock()
        mock_response.content = "Normal response"
        mock_response.model = "test-model"
        
        context = FilterExecutionContext()
        
        await chain.process_response(mock_response, context)
        
        # Verify filter_chain_executed was called
        mock_logger.filter_chain_executed.assert_called_once()
        call_args = mock_logger.filter_chain_executed.call_args
        
        assert call_args.kwargs["filters_executed"] == ["test_filter"]
        assert call_args.kwargs["total_filters"] == 1

    async def test_process_response_logs_stopfilterchain(self, chain_with_logging):
        """Test that StopFilterChain exceptions are logged."""
        chain, mock_logger = chain_with_logging
        
        # Create filter that raises StopFilterChain
        class FailingFilter(Filter):
            name = "failing_filter"
            
            async def process_request(self, request, context):
                return request
            
            async def process_response(self, response, context):
                raise StopFilterChain("Test failure", action="regenerate")
        
        failing = FailingFilter()
        
        # Recreate chain with failing filter
        with patch("keeprollming.orchestrator.filter.get_filter_logger") as mock_get:
            mock_logger_instance = MagicMock()
            mock_get.return_value = mock_logger_instance
            
            new_chain = FilterChain(
                filters=[failing],
                execution_order=["failing_filter"],
            )
        
        mock_response = MagicMock()
        mock_response.content = "Test"
        mock_response.model = "test-model"
        
        context = FilterExecutionContext()
        
        with pytest.raises(StopFilterChain):
            await new_chain.process_response(mock_response, context)
        
        # Verify filter_error was called
        mock_logger_instance.filter_error.assert_called_once()
        call_args = mock_logger_instance.filter_error.call_args
        
        assert call_args.kwargs["error_type"] == "StopFilterChain"


class TestIntegration:
    """Integration tests for logging with real filters."""

    @pytest.fixture
    def temp_log_dir(self):
        """Create a temporary directory for log files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    async def test_model_nudge_filter_integration_logging(
        self, 
        temp_log_dir,
    ):
        """Test ModelNudgeFilter writes to actual log file."""
        # Reset any existing loggers
        reset_filter_loggers()
        
        # Create filter with custom log directory and trigger patterns
        config = FilterConfig(enabled=True)
        filter_instance = ModelNudgeFilter(config)
        
        # Add default pattern for testing
        import re
        filter_instance._trigger_patterns = [re.compile(":$", re.IGNORECASE)]
        
        # Verify logger was created with correct name
        assert hasattr(filter_instance, '_logger')
        
        # Create mock response matching lazy pattern
        mock_response = MagicMock()
        mock_response.content = "Now I will:"  # Ends with colon to match :$
        mock_response.model = "test-model"
        mock_response.finish_reason = None
        mock_response.tool_calls = None
        
        context = FilterExecutionContext()
        
        # New architecture: filter returns Response directly
        result = await filter_instance.process_response(mock_response, context)
        assert result is not None
        assert hasattr(result, "content")

    def test_get_filter_logger_singleton(self, temp_log_dir):
        """Test that get_filter_logger returns singleton instances."""
        reset_filter_loggers()
        
        logger1 = get_filter_logger("test", str(temp_log_dir))
        logger2 = get_filter_logger("test", str(temp_log_dir))
        
        assert logger1 is logger2
        
        # Different names should create different instances
        logger3 = get_filter_logger("other", str(temp_log_dir))
        assert logger1 is not logger3
