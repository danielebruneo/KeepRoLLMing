"""Tests for streaming parser event emission helpers (O8 migration).

Verifies that ``events_streaming_parser`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_streaming_parser import (
    emit_streaming_parser_event,
    emit_frame_received,
    emit_events_generated,
    emit_usage_buffered,
    emit_flushed,
    emit_invalid_frame,
)


class TestEmitStreamingParserEvent:
    """Test the core emit_streaming_parser_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_streaming_parser_event("req-1", "streaming.parser.frame.received")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "streaming.parser.frame.received"
        assert event.source == EventSource(domain="streaming.parser", component="parser")
        assert event.req_id == "req-1"
        assert event.level == "DEBUG"

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("streaming.parser", emitted_events.append)
        event = emit_streaming_parser_event(
            "req-2", "streaming.parser.frame.received", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1

    def test_no_op_with_none_dispatcher(self):
        event = emit_streaming_parser_event("req-3", "streaming.parser.flushed", dispatcher=None)
        assert isinstance(event, RuntimeEvent)


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_frame_received(self):
        event = emit_frame_received("r1", frame_len=256)
        assert event.type == "streaming.parser.frame.received"
        assert event.data["frame_len"] == 256

    def test_emit_events_generated(self):
        event = emit_events_generated("r1", event_count=10)
        assert event.type == "streaming.parser.events_generated"
        assert event.data["event_count"] == 10

    def test_emit_usage_buffered(self):
        event = emit_usage_buffered("r1", usage={"prompt_tokens": 100})
        assert event.type == "streaming.parser.usage_buffered"
        assert event.data["usage"]["prompt_tokens"] == 100

    def test_emit_flushed(self):
        event = emit_flushed("r1", channel="assistant", delta_len=42)
        assert event.type == "streaming.parser.flushed"
        assert event.data["channel"] == "assistant"

    def test_emit_invalid_frame(self):
        event = emit_invalid_frame("r1", reason="bad json", frame_snippet="data: {")
        assert event.type == "streaming.parser.invalid_frame"
        assert event.level == "WARN"


class TestDispatcherIntegration:
    """Test that all streaming parser events flow through the dispatcher correctly."""

    def test_all_streaming_parser_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("streaming.parser", received.append)

        emit_frame_received("r1", dispatcher=dispatcher)
        emit_events_generated("r1", dispatcher=dispatcher)
        emit_usage_buffered("r1", dispatcher=dispatcher)
        emit_flushed("r1", dispatcher=dispatcher)
        emit_invalid_frame("r1", dispatcher=dispatcher)

        assert len(received) == 5
        types = {e.type for e in received}
        expected_types = {
            "streaming.parser.frame.received",
            "streaming.parser.events_generated",
            "streaming.parser.usage_buffered",
            "streaming.parser.flushed",
            "streaming.parser.invalid_frame",
        }
        assert types == expected_types
