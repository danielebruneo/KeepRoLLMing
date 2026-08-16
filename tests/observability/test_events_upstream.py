"""Tests for upstream event emission helpers (O7 Phase 3 migration).

Verifies that ``events_upstream`` creates correct RuntimeEvent envelopes.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_upstream import (
    emit_upstream_event,
    emit_response_received,
    emit_ctx_len,
    emit_ctx_len_fallback,
    emit_all_endpoints_failed,
    emit_override_applied,
)


class TestEmitUpstreamEvent:
    """Test the core emit_upstream_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_upstream_event("req-1", "execution.upstream.test")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.upstream.test"
        assert event.source == EventSource(domain="execution", component="upstream")

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", emitted_events.append)
        event = emit_upstream_event("req-2", "execution.upstream.test", dispatcher=dispatcher)
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_response_received(self):
        event = emit_response_received("http://x", "POST", 200, elapsed_ms=100.0)
        assert event.type == "execution.upstream.response_received"
        assert event.data["url"] == "http://x"

    def test_emit_ctx_len(self):
        event = emit_ctx_len("gpt-4", 8192, source="v1/models")
        assert event.type == "execution.upstream.ctx_len"

    def test_emit_ctx_len_fallback(self):
        event = emit_ctx_len_fallback("gpt-4", err="timeout")
        assert event.type == "execution.upstream.ctx_len_fallback"
        assert event.level == "WARN"

    def test_emit_all_endpoints_failed(self):
        event = emit_all_endpoints_failed("gpt-4")
        assert event.type == "execution.upstream.all_endpoints_failed"
        assert event.level == "WARN"

    def test_emit_override_applied(self):
        event = emit_override_applied("r1", "temperature", 0.7, 0.9)
        assert event.type == "execution.upstream.override_applied"


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_upstream_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        emit_response_received("u", "GET", 200, dispatcher=dispatcher)
        emit_ctx_len("m", 4096, dispatcher=dispatcher)
        emit_ctx_len_fallback("m", err="e", dispatcher=dispatcher)
        emit_all_endpoints_failed("m", dispatcher=dispatcher)
        emit_override_applied("r1", "k", "old", "new", dispatcher=dispatcher)

        assert len(received) == 5
