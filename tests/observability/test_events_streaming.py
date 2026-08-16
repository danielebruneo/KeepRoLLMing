"""Tests for streaming event emission helpers (O7 Phase 2 migration).

Verifies that ``events_streaming`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_streaming import (
    emit_downstream_closed,
    emit_downstream_complete,
    emit_handler_entry,
    emit_handler_error,
    emit_handler_pipeline,
    emit_pipeline_build,
    emit_pipeline_run_done,
    emit_pipeline_run_start,
    emit_stream_closed,
    emit_streaming_event,
    emit_upstream_connected,
    emit_upstream_connect,
    emit_upstream_closed,
)


class TestEmitStreamingEvent:
    """Test the core emit_streaming_event helper."""

    def test_emits_without_dispatcher(self):
        """Event is created and returned even when no dispatcher is available."""
        event = emit_streaming_event("req-1", "execution.streaming.handler_entry")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.streaming.handler_entry"
        assert event.source == EventSource(domain="streaming", component="handler")
        assert event.req_id == "req-1"
        assert event.level == "INFO"

    def test_emits_to_dispatcher(self):
        """Event is emitted through the dispatcher when provided."""
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []

        def capture(event: RuntimeEvent) -> None:
            emitted_events.append(event)

        dispatcher.subscribe("execution", capture)
        event = emit_streaming_event(
            "req-2", "execution.streaming.handler_entry", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1
        assert emitted_events[0].type == "execution.streaming.handler_entry"
        assert emitted_events[0].req_id == "req-2"

    def test_no_op_with_none_dispatcher(self):
        """Passing None as dispatcher is a no-op (no exception)."""
        event = emit_streaming_event("req-3", "execution.streaming.handler_entry", dispatcher=None)
        assert isinstance(event, RuntimeEvent)

    def test_custom_level(self):
        """Events can be emitted with non-default levels."""
        event = emit_streaming_event(
            "req-4", "execution.streaming.handler_error", level="ERROR"
        )
        assert event.level == "ERROR"


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_pipeline_build(self):
        event = emit_pipeline_build("r1", route_name="default", built=True,
                                    filter_keys=["TLS"])
        assert event.type == "execution.streaming.pipeline_build"
        assert event.data["route_name"] == "default"
        assert event.data["built"] is True

    def test_emit_handler_entry(self):
        event = emit_handler_entry("r1", route_name="default", filters=["timestamp"])
        assert event.type == "execution.streaming.handler_entry"
        assert event.data["route_name"] == "default"
        assert event.data["filters"] == ["timestamp"]

    def test_emit_handler_pipeline(self):
        event = emit_handler_pipeline("r1", pipeline_built=True)
        assert event.type == "execution.streaming.handler_pipeline"
        assert event.data["pipeline_built"] is True

    def test_emit_upstream_connect(self):
        event = emit_upstream_connect("r1", "http://localhost:8080/v1")
        assert event.type == "execution.streaming.upstream_connect"
        assert event.level == "DEBUG"
        assert event.data["url"] == "http://localhost:8080/v1"

    def test_emit_upstream_connected(self):
        event = emit_upstream_connected("r1", status=200)
        assert event.type == "execution.streaming.upstream_connected"
        assert event.level == "DEBUG"
        assert event.data["status"] == 200

    def test_emit_upstream_closed(self):
        event = emit_upstream_closed("r1", reason="normal", total_chunks=42)
        assert event.type == "execution.streaming.upstream_closed"
        assert event.data["reason"] == "normal"
        assert event.data["total_chunks"] == 42

    def test_emit_pipeline_run_start(self):
        event = emit_pipeline_run_start("r1", route="default")
        assert event.type == "execution.streaming.pipeline_run_start"
        assert event.data["route"] == "default"

    def test_emit_pipeline_run_done(self):
        event = emit_pipeline_run_done("r1", total_yielded=100, execution_usage=True)
        assert event.type == "execution.streaming.pipeline_run_done"
        assert event.data["total_yielded"] == 100
        assert event.data["execution_usage"] is True

    def test_emit_downstream_closed(self):
        event = emit_downstream_closed("r1", reason="client_disconnect", chunks_yielded=5)
        assert event.type == "execution.streaming.downstream_closed"
        assert event.data["reason"] == "client_disconnect"
        assert event.data["chunks_yielded"] == 5

    def test_emit_handler_error(self):
        event = emit_handler_error("r1", error="ConnectionTimeout")
        assert event.type == "execution.streaming.handler_error"
        assert event.level == "ERROR"
        assert event.data["error"] == "ConnectionTimeout"

    def test_emit_downstream_complete(self):
        event = emit_downstream_complete("r1", total_yielded=42)
        assert event.type == "execution.streaming.downstream_complete"
        assert event.data["total_yielded"] == 42

    def test_emit_stream_closed(self):
        event = emit_stream_closed("r1", chunks_yielded=42)
        assert event.type == "execution.streaming.stream_closed"
        assert event.level == "DEBUG"
        assert event.data["chunks_yielded"] == 42


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_streaming_events_dispatched(self):
        """All convenience wrappers emit through a subscribed dispatcher."""
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        # Call every wrapper, passing the dispatcher
        emit_pipeline_build("r1", "default", True, ["TLS"], dispatcher=dispatcher)
        emit_handler_entry("r1", "default", True, dispatcher=dispatcher)
        emit_handler_pipeline("r1", True, dispatcher=dispatcher)
        emit_upstream_connect("r1", "http://x", dispatcher=dispatcher)
        emit_upstream_connected("r1", 200, dispatcher=dispatcher)
        emit_upstream_closed("r1", "normal", 0, dispatcher=dispatcher)
        emit_pipeline_run_start("r1", "default", dispatcher=dispatcher)
        emit_pipeline_run_done("r1", 0, False, dispatcher=dispatcher)
        emit_downstream_closed("r1", "client_disconnect", 0, dispatcher=dispatcher)
        emit_handler_error("r1", "err", dispatcher=dispatcher)
        emit_downstream_complete("r1", 0, dispatcher=dispatcher)
        emit_stream_closed("r1", 0, dispatcher=dispatcher)

        # All should have been captured by the dispatcher
        assert len(received) == 12
        types = {e.type for e in received}
        expected_types = {
            "execution.streaming.pipeline_build",
            "execution.streaming.handler_entry",
            "execution.streaming.handler_pipeline",
            "execution.streaming.upstream_connect",
            "execution.streaming.upstream_connected",
            "execution.streaming.upstream_closed",
            "execution.streaming.pipeline_run_start",
            "execution.streaming.pipeline_run_done",
            "execution.streaming.downstream_closed",
            "execution.streaming.handler_error",
            "execution.streaming.downstream_complete",
            "execution.streaming.stream_closed",
        }
        assert types == expected_types
