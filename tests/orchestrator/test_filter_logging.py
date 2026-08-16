"""Tests for filter pipeline event emission (O6 migration).

Phase P6 cleanup: FilterLogger tests removed. FilterLogger shim has been
retired; all filters now use RuntimeEvent emission via orchestrator/filters/events.py.
This file now tests only the RuntimeEvent-based filter event emission path.
"""

import pytest

from keeprollming.observability import EventDispatcher, RuntimeEvent
from keeprollming.orchestrator.filters.events import emit_filter_event


class TestFilterEventEmission:
    """Tests for RuntimeEvent-based filter event emission."""

    def test_emit_filter_event_basic(self):
        """Test that emit_filter_event creates and emits a RuntimeEvent."""
        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        # Subscribe to "filter" namespace prefix (matches filter.*)
        dispatcher.subscribe("filter", capture)

        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        emit_filter_event(
            context,
            component="model_nudge",
            event_type="filter.nudge.detected",
            trigger_pattern=":$",
            response_content="Test:",
            nudge_attempt=1,
        )

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.type == "filter.nudge.detected"
        assert event.source.domain == "filter"
        assert event.source.component == "model_nudge"
        assert event.req_id == "test-req-123"
        assert event.data["trigger_pattern"] == ":$"

    def test_emit_filter_event_no_dispatcher(self):
        """Test that emit_filter_event creates event but doesn't emit when no dispatcher."""
        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = None

        # Should not raise; returns event but doesn't emit it
        result = emit_filter_event(
            context,
            component="model_nudge",
            event_type="filter.nudge.detected",
        )
        assert result is not None
        assert result.type == "filter.nudge.detected"

    def test_emit_system_prompt_events(self):
        """Test system prompt event emission helpers."""
        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        dispatcher.subscribe("filter", capture)

        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        from keeprollming.orchestrator.filters.events import (
            emit_system_prompt_inserted,
            emit_system_prompt_overridden,
            emit_system_prompt_prepended,
        )

        emit_system_prompt_inserted(context, "You are a helpful assistant")
        emit_system_prompt_overridden(context, "New system prompt", old_length=50)
        emit_system_prompt_prepended(context, "Prefix:", old_length=30)

        assert len(captured_events) == 3
        assert captured_events[0].type == "filter.system_prompt.inserted"
        assert captured_events[1].type == "filter.system_prompt.overridden"
        assert captured_events[2].type == "filter.system_prompt.prepended"

    def test_emit_nudge_events(self):
        """Test nudge event emission helper."""
        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        dispatcher.subscribe("filter", capture)

        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        from keeprollming.orchestrator.filters.events import emit_nudge_detected

        emit_nudge_detected(
            context,
            trigger_pattern=":$",
            response_content="Now I will:",
            nudge_attempt=1,
            action="nudge",
            max_attempts=3,
        )

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.type == "filter.nudge.detected"
        assert event.source.component == "model_nudge"
        assert event.data["nudge_attempt"] == 1

    def test_emit_tool_loop_events(self):
        """Test tool loop event emission helpers."""
        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        dispatcher.subscribe("filter", capture)

        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        from keeprollming.orchestrator.filters.events import (
            emit_tool_loop_detected,
            emit_tls_intervention,
            emit_tls_retry,
            emit_tls_fallback,
        )

        emit_tool_loop_detected(context, "search", "hash123", attempt=1)
        emit_tls_intervention(context, messages_count=5)
        emit_tls_retry(context, model="gpt-4", messages_count=6)
        emit_tls_fallback(context, reason="max_attempts_exceeded")

        assert len(captured_events) == 4
        assert captured_events[0].type == "filter.tool_loop.detected"
        assert captured_events[1].type == "filter.tool_loop.intervention"
        assert captured_events[2].type == "filter.tool_loop.retry"
        assert captured_events[3].type == "filter.tool_loop.fallback"

    def test_emit_reasoning_loop_events(self):
        """Test reasoning loop event emission helpers."""
        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        dispatcher.subscribe("filter", capture)

        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        from keeprollming.orchestrator.filters.events import (
            emit_reasoning_loop_detected,
            emit_rls_intervention,
            emit_rls_fallback,
        )

        emit_reasoning_loop_detected(context, "Let me think about this...")
        emit_rls_intervention(context, messages_count=4)
        emit_rls_fallback(context)

        assert len(captured_events) == 3
        assert captured_events[0].type == "filter.reasoning_loop.detected"
        assert captured_events[1].type == "filter.reasoning_loop.intervention"
        assert captured_events[2].type == "filter.reasoning_loop.fallback"

    def test_emit_tool_rewrite_event(self):
        """Test tool rewrite event emission helper."""
        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        dispatcher.subscribe("filter", capture)

        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        from keeprollming.orchestrator.filters.events import emit_tool_rewrite_applied

        emit_tool_rewrite_applied(context, "search", original_length=500, cleaned_length=200)

        assert len(captured_events) == 1
        event = captured_events[0]
        assert event.type == "filter.tool_rewrite.applied"
        assert event.source.component == "tool_rewrite"
        assert event.data["tool_name"] == "search"

    def test_emit_filter_chain_events(self):
        """Test filter chain event emission helpers."""
        dispatcher = EventDispatcher()
        captured_events = []

        def capture(event: RuntimeEvent):
            captured_events.append(event)

        dispatcher.subscribe("filter", capture)

        from unittest.mock import MagicMock

        context = MagicMock(spec="FilterExecutionContext")
        context.req_id = "test-req-123"
        context.event_dispatcher = dispatcher

        from keeprollming.orchestrator.filters.events import (
            emit_filter_chain_executed,
            emit_filter_error,
            emit_filter_disabled,
        )

        emit_filter_chain_executed(
            context,
            filters_executed=["system_prompt", "model_nudge"],
            total_filters=2,
            nudge_count=1,
        )
        emit_filter_error(context, error_type="ValidationError", message="Invalid config")
        emit_filter_disabled(context)

        assert len(captured_events) == 3
        assert captured_events[0].type == "filter.chain.executed"
        assert captured_events[1].type == "filter.error"
        assert captured_events[2].type == "filter.disabled"


# ── Phase P6 cleanup: FilterLogger backward compat test removed ────
# The test_filter_logger_still_works test was removed because FilterLogger
# has been retired. Tests should now verify RuntimeEvent-based emission.
