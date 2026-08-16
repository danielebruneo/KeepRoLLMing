"""Tests for routing event emission helpers (O8 migration).

Verifies that ``events_routing`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_routing import (
    emit_routing_event,
    emit_started as emit_routing_started,
    emit_resolved as emit_routing_resolved,
    emit_failed as emit_routing_failed,
)


class TestEmitRoutingEvent:
    """Test the core emit_routing_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_routing_event("req-1", "routing.resolution.started")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "routing.resolution.started"
        assert event.source == EventSource(domain="routing", component="resolution")
        assert event.req_id == "req-1"
        assert event.level == "INFO"

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("routing", emitted_events.append)
        event = emit_routing_event(
            "req-2", "routing.resolution.started", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1
        assert emitted_events[0].type == "routing.resolution.started"

    def test_no_op_with_none_dispatcher(self):
        event = emit_routing_event("req-3", "routing.resolution.resolved", dispatcher=None)
        assert isinstance(event, RuntimeEvent)


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_started(self):
        event = emit_routing_started("r1", "gpt-4")
        assert event.type == "routing.resolution.started"
        assert event.data["client_model"] == "gpt-4"

    def test_emit_resolved(self):
        event = emit_routing_resolved(
            "r1", "gpt-4", "default", "gpt-4", "gpt-4"
        )
        assert event.type == "routing.resolution.resolved"
        assert event.data["resolved_route"] == "default"

    def test_emit_failed(self):
        event = emit_routing_failed("r1", "unknown-model", error="no route")
        assert event.type == "routing.resolution.failed"
        assert event.level == "ERROR"


class TestDispatcherIntegration:
    """Test that all routing events flow through the dispatcher correctly."""

    def test_all_routing_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("routing", received.append)

        emit_routing_started("r1", "m", dispatcher=dispatcher)
        emit_routing_resolved("r1", "m", "r", "m", "m", dispatcher=dispatcher)
        emit_routing_failed("r1", "m", error="e", dispatcher=dispatcher)

        assert len(received) == 3
        types = {e.type for e in received}
        expected_types = {
            "routing.resolution.started",
            "routing.resolution.resolved",
            "routing.resolution.failed",
        }
        assert types == expected_types
