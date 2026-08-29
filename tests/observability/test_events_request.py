"""Tests for request event emission helpers (O8 migration).

Verifies that ``events_request`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""


from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_request import (
    emit_cancelled,
    emit_completed,
    emit_preprocessing_completed,
    emit_preprocessing_started,
    emit_received,
    emit_request_event,
)
from keeprollming.observability.events_request import (
    emit_failed as emit_request_failed,
)


class TestEmitRequestEvent:
    """Test the core emit_request_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_request_event("req-1", "request.lifecycle.received")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "request.lifecycle.received"
        assert event.source == EventSource(domain="request", component="lifecycle")
        assert event.req_id == "req-1"
        assert event.level == "INFO"

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("request", emitted_events.append)
        event = emit_request_event(
            "req-2", "request.lifecycle.received", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1
        assert emitted_events[0].type == "request.lifecycle.received"

    def test_no_op_with_none_dispatcher(self):
        event = emit_request_event("req-3", "request.lifecycle.completed", dispatcher=None)
        assert isinstance(event, RuntimeEvent)

    def test_custom_level(self):
        event = emit_request_event("req-4", "request.lifecycle.failed", level="ERROR")
        assert event.level == "ERROR"


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_received(self):
        event = emit_received("r1", "gpt-4", stream=True, endpoint="/v1/chat/completions")
        assert event.type == "request.lifecycle.received"
        assert event.data["client_model"] == "gpt-4"
        assert event.data["stream"] is True

    def test_emit_preprocessing_started(self):
        event = emit_preprocessing_started("r1", endpoint="/v1/chat/completions")
        assert event.type == "request.lifecycle.preprocessing.started"
        assert event.data["endpoint"] == "/v1/chat/completions"

    def test_emit_preprocessing_completed(self):
        event = emit_preprocessing_completed("r1", stream=True)
        assert event.type == "request.lifecycle.preprocessing.completed"
        assert event.data["stream"] is True

    def test_emit_completed(self):
        event = emit_completed("r1", status=200, elapsed_ms=150.0)
        assert event.type == "request.lifecycle.completed"
        assert event.data["status"] == 200

    def test_emit_failed(self):
        event = emit_request_failed("r1", "upstream timeout", status=504)
        assert event.type == "request.lifecycle.failed"
        assert event.level == "ERROR"
        assert event.data["error"] == "upstream timeout"

    def test_emit_cancelled(self):
        event = emit_cancelled("r1", reason="client disconnect", level="BASIC")
        assert event.type == "request.lifecycle.cancelled"
        assert event.level == "BASIC"
        assert event.data["reason"] == "client disconnect"


class TestDispatcherIntegration:
    """Test that all request events flow through the dispatcher correctly."""

    def test_all_request_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("request", received.append)

        emit_received("r1", "m", stream=True, dispatcher=dispatcher)
        emit_preprocessing_started("r1", dispatcher=dispatcher)
        emit_preprocessing_completed("r1", dispatcher=dispatcher)
        emit_completed("r1", status=200, dispatcher=dispatcher)
        emit_request_failed("r1", "err", dispatcher=dispatcher)
        emit_cancelled("r1", reason="timeout", dispatcher=dispatcher)

        assert len(received) == 6
        types = {e.type for e in received}
        expected_types = {
            "request.lifecycle.received",
            "request.lifecycle.preprocessing.started",
            "request.lifecycle.preprocessing.completed",
            "request.lifecycle.completed",
            "request.lifecycle.failed",
            "request.lifecycle.cancelled",
        }
        assert types == expected_types
