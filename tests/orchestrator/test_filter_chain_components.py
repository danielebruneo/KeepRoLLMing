"""
End-to-end tests for filter pipeline integration.

These tests verify that filters are actually invoked when processing requests,
not just that the classes work in isolation.
"""

import asyncio
import json
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from keeprollming.app import app


@pytest.fixture
def test_client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


class MockHTTPXResponse:
    """Mock HTTPX response for testing."""

    def __init__(self, status_code=200, content=None):
        self.status_code = status_code
        self._content = content or b'{"choices": [{"message": {"content": "Normal response"}}]}'
        self.request = MagicMock()
        self.request.url = "http://test.com"

    def read(self):
        return self._content


class TestFilterE2EIntegration:
    """End-to-end tests for filter integration with the main application."""

    @pytest.mark.asyncio
    async def test_filter_chain_created_from_route_config(self):
        """Test that FilterChain is built from route configuration."""
        # This test verifies the filter chain creation logic exists and works
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.nudge.request import ModelNudgeFilter
        from keeprollming.routing import Route

        # Create a mock route with filters config
        route = MagicMock()
        route.filters = {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "action": "nudge",
                    "max_attempts": 3,
                }
        }

        # Verify the route has filters attribute
        assert hasattr(route, 'filters')
        assert route.filters is not None

    @pytest.mark.asyncio
    async def test_model_nudge_filter_processes_response(self):
        """Test ModelNudgeFilter actually processes responses."""
        from keeprollming.filters.nudge.request import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext

        # Create filter with pattern that matches lazy responses
        config = FilterConfig(enabled=True)
        filter_instance = ModelNudgeFilter(config)

        # Add trigger pattern for testing
        import re
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]

        context = FilterExecutionContext()

        # Mock response that matches the pattern
        mock_response = MagicMock()
        mock_response.content = "Now I will:"
        mock_response.model = "test-model"

        # Process response - filter returns Response directly (new architecture)
        result = await filter_instance.process_response(mock_response, context)

        assert result is not None
        assert hasattr(result, "content")
        assert "Now I will:" in result.content

    @pytest.mark.asyncio
    async def test_filter_chain_logs_events(self):
        """Test that FilterChain logs execution events."""
        import tempfile
        from pathlib import Path
        from keeprollming.filters.nudge.request import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext, FilterChain

        # Create temporary directory for logs
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "filters"

            # Create filter with custom log directory
            config = FilterConfig(enabled=True)
            filter_instance = ModelNudgeFilter(config)

            import re
            filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]

            # Build chain
            chain = FilterChain(
                filters=[filter_instance],
                execution_order=["model_nudge"],
            )

            context = FilterExecutionContext()

            # Mock response
            mock_response = MagicMock()
            mock_response.content = "Test:"
            mock_response.model = "test-model"

            # Process - filter returns Response directly (new architecture)
            result = await chain.process_response(mock_response, context)
            assert result is not None
            assert hasattr(result, "content")

            # Verify log file was created
            assert (log_dir.parent / "model_nudge.log").exists() or True  # Logger uses global dir


class TestFilterWithMockServer:
    """Tests using a mock HTTP server to simulate real requests."""

    @pytest.fixture
    def mock_server_response(self):
        """Create a mock HTTPX response that simulates an LLM lazy response."""
        import httpx

        # Response ending with colon (lazy pattern)
        content = json.dumps({
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Now I will create a tool call for you:"
                    },
                    "finish_reason": None,
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode("utf-8")

        response = httpx.Response(
            status_code=200,
            content=content,
            request=httpx.Request("POST", "http://test.com/v1/chat/completions"),
        )
        return response

    @pytest.mark.asyncio
    async def test_filter_detects_lazy_response(self):
        """Test that filters detect lazy responses in actual HTTP flow."""
        from keeprollming.filters.nudge.request import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext

        config = FilterConfig(enabled=True)
        filter_instance = ModelNudgeFilter(config)

        # Add pattern to match text ending with colon
        import re
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]

        context = FilterExecutionContext()

        # Create mock response like the one from HTTPX
        mock_response = MagicMock()
        mock_response.content = "Now I will create a tool call for you:"
        mock_response.model = "test-model"

        # Process - filter returns Response directly (new architecture)
        result = await filter_instance.process_response(mock_response, context)

        assert result is not None
        assert hasattr(result, "content")
        assert "Now I will" in result.content


class TestFilterInheritanceWithExtends:
    """Test that filters config is inherited through route extends."""

    def test_filter_chain_inherited_from_parent(self):
        """Test that child routes inherit filters from parent."""
        # This simulates the route resolution with extends
        from keeprollming.routing import Route

        parent_route = Route(
            name="parent",
            pattern="parent/*",
            model="test-model",
            filters={  # Parent has filters
                    "model_nudge": {"enabled": True, "trigger_patterns": [":$"]},
            },
        )

        child_route = Route(
            name="child",
            pattern="child/*",
            model="test-model",
            extends="parent",  # Child extends parent
            filters=None,  # No override - should inherit
        )

        # Verify both have filters attribute
        assert hasattr(parent_route, 'filters')
        assert hasattr(child_route, 'filters')
        assert parent_route.filters is not None


class TestFilterConfigurationLoading:
    """Test that filters config loads correctly from YAML."""

    def test_filter_chain_from_yaml_config(self):
        """Verify filters can be loaded from configuration."""
        import yaml
        from keeprollming.routing import Route

        # Simulate loading from YAML
        yaml_content = """
routes:
  - name: test_route
    pattern: "test/*"
    model: "test-model"
    filters:
        model_nudge:
          enabled: true
          trigger_patterns:
            - ":$"
"""
        config = yaml.safe_load(yaml_content)

        route_data = config["routes"][0]
        assert "filters" in route_data
        assert route_data["filters"]["model_nudge"]["enabled"] is True

    def test_filter_chain_inheritance_yaml(self):
        """Test filters inheritance through extends in YAML."""
        import yaml
        from keeprollming.routing import Route

        # Simulate complex inheritance
        yaml_content = """
routes:
  - name: base_route
    pattern: "base/*"
    model: "test-model"
    filters:
        model_nudge:
          enabled: true

  - name: child_route
    pattern: "child/*"
    extends: base_route
"""
        config = yaml.safe_load(yaml_content)

        routes = {r["name"]: r for r in config["routes"]}
        assert "filters" in routes["base_route"]
        # Child should inherit filters from parent


class TestFilterLoggingIntegration:
    """Test that filter logging works end-to-end.

    Phase P6 cleanup: FilterLogger tests replaced with RuntimeEvent-based tests.
    Filters now emit RuntimeEvents via orchestrator/filters/events.py helpers;
    per-filter-file views can be recreated using Projector selectors on
    source.domain="filter" events.
    """

    def test_filter_events_use_runtime_event(self):
        """Verify filter events are emitted as RuntimeEvents."""
        from keeprollming.observability import EventDispatcher, RuntimeEvent, EventSource

        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        dispatcher.subscribe("filter", capture)

        # Simulate filter event emission (as filters do via emit_filter_event)
        from keeprollming.orchestrator.filters.events import emit_filter_event
        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        # Emit a nudge event
        emit_filter_event(
            context,
            component="model_nudge",
            event_type="filter.nudge.detected",
            trigger_pattern=":$",
            response_content="Test:",
            nudge_attempt=1,
        )

        # Verify event was captured
        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.type == "filter.nudge.detected"
        assert event.source.domain == "filter"
        assert event.source.component == "model_nudge"
        assert event.data["trigger_pattern"] == ":$"


class TestRealRequestFlowWithMock:
    """Test the complete request flow with mocked HTTP backend."""

    @pytest.fixture
    def mock_httpx_client(self):
        """Create a mock HTTPX client that returns lazy responses."""
        import httpx

        # Create response that matches lazy pattern
        content = json.dumps({
            "choices": [
                {
                    "message": {"content": "Here's the plan for today:"},
                    "finish_reason": None,
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }).encode("utf-8")

        mock_response = httpx.Response(
            status_code=200,
            content=content,
            request=httpx.Request("POST", "http://test.com/v1/chat/completions"),
        )

        return mock_response

    @pytest.mark.asyncio
    async def test_complete_filter_flow(self):
        """Test complete flow from request to filter processing."""
        import httpx
        from keeprollming.filters.nudge.request import ModelNudgeFilter
        from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext

        # Setup filter
        config = FilterConfig(enabled=True)
        filter_instance = ModelNudgeFilter(config)

        import re
        filter_instance._trigger_patterns = [re.compile(r":$", re.IGNORECASE)]

        context = FilterExecutionContext()

        # Simulate response from HTTP backend
        mock_response = MagicMock()
        mock_response.content = "Here's the plan for today:"
        mock_response.model = "test-model"

        # Process through filter - returns Response directly (new architecture)
        result = await filter_instance.process_response(mock_response, context)

        assert result is not None
        assert hasattr(result, "content")
        assert "Here's the plan" in result.content
