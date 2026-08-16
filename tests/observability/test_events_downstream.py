"""Tests for downstream event emission helpers (O8 migration).

Verifies that ``events_downstream`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_downstream import (
    emit_downstream_event,
    emit_chunk_sent,
    emit_delivery_completed,
    emit_delivery_closed,
    emit_delivery_failed,
)


class TestEmitDownstreamEvent:
    """Test the core emit_downstream_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_downstream_event("req-1", "downstream.delivery.chunk.sent")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "downstream.delivery.chunk.sent"
        assert event.source == EventSource(domain="downstream", component="delivery")
        assert event.req_id == "req-1"
        assert event.level == "INFO"

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("downstream", emitted_events.append)
        event = emit_downstream_event(
            "req-2", "downstream.delivery.chunk.sent", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1

    def test_no_op_with_none_dispatcher(self):
        event = emit_downstream_event("req-3", "downstream.delivery.completed", dispatcher=None)
        assert isinstance(event, RuntimeEvent)


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_chunk_sent(self):
        event = emit_chunk_sent("r1", chunk_index=5, delta_len=42)
        assert event.type == "downstream.delivery.chunk.sent"
        assert event.data["chunk_index"] == 5

    def test_emit_delivery_completed(self):
        event = emit_delivery_completed("r1", total_chunks=100, elapsed_ms=500.0)
        assert event.type == "downstream.delivery.completed"
        assert event.data["total_chunks"] == 100

    def test_emit_delivery_closed(self):
        event = emit_delivery_closed("r1", finish_reason="stop")
        assert event.type == "downstream.delivery.closed"
        assert event.data["finish_reason"] == "stop"

    def test_emit_delivery_failed(self):
        event = emit_delivery_failed("r1", "connection reset", status=0)
        assert event.type == "downstream.delivery.failed"
        assert event.level == "ERROR"


class TestDispatcherIntegration:
    """Test that all downstream events flow through the dispatcher correctly."""

    def test_all_downstream_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("downstream", received.append)

        emit_chunk_sent("r1", dispatcher=dispatcher)
        emit_delivery_completed("r1", dispatcher=dispatcher)
        emit_delivery_closed("r1", dispatcher=dispatcher)
        emit_delivery_failed("r1", "err", dispatcher=dispatcher)

        assert len(received) == 4
        types = {e.type for e in received}
        expected_types = {
            "downstream.delivery.chunk.sent",
            "downstream.delivery.completed",
            "downstream.delivery.closed",
            "downstream.delivery.failed",
        }
        assert types == expected_types
