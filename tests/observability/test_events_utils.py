"""Tests for utils event emission helpers (O7 Phase 3 migration).

Verifies that ``events_utils`` creates correct RuntimeEvent envelopes.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_utils import (
    emit_utils_event,
    emit_dump_failed,
)


class TestEmitUtilsEvent:
    """Test the core emit_utils_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_utils_event("req-1", "execution.utils.test")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.utils.test"
        assert event.source == EventSource(domain="execution", component="utils")

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", emitted_events.append)
        event = emit_utils_event("req-2", "execution.utils.test", dispatcher=dispatcher)
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_dump_failed(self):
        event = emit_dump_failed("r1", error="payload too large")
        assert event.type == "execution.utils.dump_failed"
        assert event.level == "WARN"


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_utils_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        emit_dump_failed("r1", error="test", dispatcher=dispatcher)

        assert len(received) == 1
