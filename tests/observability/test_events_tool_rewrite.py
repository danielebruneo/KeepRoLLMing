"""Tests for tool-rewrite event emission helpers (O8 migration).

Verifies that ``events_tool_rewrite`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_tool_rewrite import (
    emit_tool_rewrite_event,
    emit_parse_error,
    emit_streaming_error,
    emit_body_error,
)


class TestEmitToolRewriteEvent:
    """Test the core emit_tool_rewrite_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_tool_rewrite_event("req-1", "execution.tool_rewrite.parse_error")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.tool_rewrite.parse_error"
        assert event.source == EventSource(domain="execution", component="tool_rewrite")
        assert event.req_id == "req-1"
        assert event.level == "ERROR"

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution.tool_rewrite", emitted_events.append)
        event = emit_tool_rewrite_event(
            "req-2", "execution.tool_rewrite.parse_error", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1

    def test_no_op_with_none_dispatcher(self):
        event = emit_tool_rewrite_event("req-3", "execution.tool_rewrite.body_error", dispatcher=None)
        assert isinstance(event, RuntimeEvent)


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_parse_error(self):
        event = emit_parse_error("r1", error="invalid XML", traceback="Traceback...")
        assert event.type == "execution.tool_rewrite.parse_error"
        assert event.data["error"] == "invalid XML"
        assert event.level == "ERROR"

    def test_emit_streaming_error(self):
        event = emit_streaming_error("r1", error="decode failed")
        assert event.type == "execution.tool_rewrite.streaming_error"
        assert event.data["error"] == "decode failed"

    def test_emit_body_error(self):
        event = emit_body_error("r1", error="missing choices")
        assert event.type == "execution.tool_rewrite.body_error"
        assert event.data["error"] == "missing choices"


class TestDispatcherIntegration:
    """Test that all tool-rewrite events flow through the dispatcher correctly."""

    def test_all_tool_rewrite_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution.tool_rewrite", received.append)

        emit_parse_error("r1", dispatcher=dispatcher)
        emit_streaming_error("r1", dispatcher=dispatcher)
        emit_body_error("r1", dispatcher=dispatcher)

        assert len(received) == 3
        types = {e.type for e in received}
        expected_types = {
            "execution.tool_rewrite.parse_error",
            "execution.tool_rewrite.streaming_error",
            "execution.tool_rewrite.body_error",
        }
        assert types == expected_types
