"""Tests for execution accounting event emission helpers (O8 migration).

Verifies that ``events_execution_accounting`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_execution_accounting import (
    emit_execution_accounting_event,
    emit_usage_captured,
    emit_attempt_recorded,
    emit_usage_finalized,
)


class TestEmitExecutionAccountingEvent:
    """Test the core emit_execution_accounting_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_execution_accounting_event(
            "req-1", "execution.accounting.usage.captured"
        )
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.accounting.usage.captured"
        assert event.source == EventSource(domain="execution", component="accounting")
        assert event.req_id == "req-1"
        assert event.level == "INFO"

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution.accounting", emitted_events.append)
        event = emit_execution_accounting_event(
            "req-2", "execution.accounting.usage.captured", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1

    def test_no_op_with_none_dispatcher(self):
        event = emit_execution_accounting_event(
            "req-3", "execution.accounting.usage.finalized", dispatcher=None
        )
        assert isinstance(event, RuntimeEvent)


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_usage_captured(self):
        event = emit_usage_captured("r1", prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert event.type == "execution.accounting.usage.captured"
        assert event.data["prompt_tokens"] == 100
        assert event.data["completion_tokens"] == 50

    def test_emit_attempt_recorded(self):
        event = emit_attempt_recorded("r1", model="gpt-4", attempt=2, tokens=150)
        assert event.type == "execution.accounting.usage.attempt_recorded"
        assert event.data["model"] == "gpt-4"
        assert event.data["attempt"] == 2

    def test_emit_usage_finalized(self):
        event = emit_usage_finalized("r1", total_attempts=3, total_cost=0.05)
        assert event.type == "execution.accounting.usage.finalized"
        assert event.data["total_attempts"] == 3
        assert event.data["total_cost"] == 0.05


class TestDispatcherIntegration:
    """Test that all execution accounting events flow through the dispatcher correctly."""

    def test_all_accounting_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution.accounting", received.append)

        emit_usage_captured("r1", dispatcher=dispatcher)
        emit_attempt_recorded("r1", dispatcher=dispatcher)
        emit_usage_finalized("r1", dispatcher=dispatcher)

        assert len(received) == 3
        types = {e.type for e in received}
        expected_types = {
            "execution.accounting.usage.captured",
            "execution.accounting.usage.attempt_recorded",
            "execution.accounting.usage.finalized",
        }
        assert types == expected_types
