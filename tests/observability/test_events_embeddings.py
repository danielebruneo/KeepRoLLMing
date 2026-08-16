"""Tests for embeddings event emission helpers (O7 Phase 3 migration).

Verifies that ``events_embeddings`` creates correct RuntimeEvent envelopes.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_embeddings import (
    emit_embedding_event,
    emit_request,
    emit_request_debug,
    emit_failed,
    emit_timeout,
)


class TestEmitEmbeddingEvent:
    """Test the core emit_embedding_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_embedding_event("req-1", "execution.embeddings.test")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.embeddings.test"
        assert event.source == EventSource(domain="execution", component="embeddings")

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", emitted_events.append)
        event = emit_embedding_event("req-2", "execution.embeddings.test", dispatcher=dispatcher)
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_request(self):
        event = emit_request("r1", model="text-embedding-3-small", input_length=10)
        assert event.type == "execution.embeddings.request"

    def test_emit_request_debug(self):
        event = emit_request_debug("r1", body_json={"input": ["hello"]})
        assert event.type == "execution.embeddings.request_debug"

    def test_emit_failed(self):
        event = emit_failed("r1", error="connection refused", upstream_url="http://x")
        assert event.type == "execution.embeddings.failed"
        assert event.level == "ERROR"

    def test_emit_timeout(self):
        event = emit_timeout("r1", upstream_url="http://x")
        assert event.type == "execution.embeddings.timeout"
        assert event.level == "ERROR"


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_embeddings_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        emit_request("r1", dispatcher=dispatcher)
        emit_request_debug("r1", dispatcher=dispatcher)
        emit_failed("r1", error="e", dispatcher=dispatcher)
        emit_timeout("r1", dispatcher=dispatcher)

        assert len(received) == 4
