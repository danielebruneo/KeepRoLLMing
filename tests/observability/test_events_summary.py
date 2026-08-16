"""Tests for summary event emission helpers (O7 Phase 3 migration).

Verifies that ``events_summary`` creates correct RuntimeEvent envelopes.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_summary import (
    emit_summary_event,
    emit_plan,
    emit_request,
    emit_reply,
    emit_retry_exhausted,
    emit_incremental_retry_exhausted,
    emit_preflight_chunking,
    emit_incremental_preflight_chunking,
    emit_preflight_forced_split,
    emit_incremental_preflight_forced_split,
    emit_no_progress_abort,
    emit_incremental_no_progress_abort,
    emit_overflow_chunking,
    emit_incremental_overflow_chunking,
    emit_overflow_forced_split,
    emit_incremental_overflow_forced_split,
    emit_http_retry_chunking,
    emit_incremental_http_retry_chunking,
    emit_http_retry_forced_split,
    emit_incremental_http_retry_forced_split,
)


class TestEmitSummaryEvent:
    """Test the core emit_summary_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_summary_event("req-1", "execution.summary.test")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.summary.test"
        assert event.source == EventSource(domain="execution", component="summary")

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", emitted_events.append)
        event = emit_summary_event("req-2", "execution.summary.test", dispatcher=dispatcher)
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_plan(self):
        event = emit_plan(should=True, reason="threshold", threshold=4096)
        assert event.type == "execution.summary.plan"

    def test_emit_request(self):
        event = emit_request("r1", summary_model="gpt-4", middle_count=10)
        assert event.type == "execution.summary.request"

    def test_emit_reply(self):
        event = emit_reply("r1", elapsed_ms=50.0, summary_chars=200)
        assert event.type == "execution.summary.reply"

    def test_emit_retry_exhausted(self):
        event = emit_retry_exhausted("r1", attempts=3, max_attempts=3)
        assert event.type == "execution.summary.retry_exhausted"
        assert event.level == "ERROR"

    def test_emit_incremental_retry_exhausted(self):
        event = emit_incremental_retry_exhausted("r1", attempts=2, new_messages_count=5)
        assert event.type == "execution.summary.incremental_retry_exhausted"

    def test_emit_preflight_chunking(self):
        event = emit_preflight_chunking("r1", chunks=3, est_tokens=5000)
        assert event.type == "execution.summary.preflight_chunking"
        assert event.level == "WARN"

    def test_emit_incremental_preflight_chunking(self):
        event = emit_incremental_preflight_chunking("r1", chunks=2)
        assert event.type == "execution.summary.incremental_preflight_chunking"

    def test_emit_preflight_forced_split(self):
        event = emit_preflight_forced_split("r1", chunks=3)
        assert event.type == "execution.summary.preflight_forced_split"

    def test_emit_incremental_preflight_forced_split(self):
        event = emit_incremental_preflight_forced_split("r1", chunks=2)
        assert event.type == "execution.summary.incremental_preflight_forced_split"

    def test_emit_no_progress_abort(self):
        event = emit_no_progress_abort("r1", attempts=2, err="single chunk")
        assert event.type == "execution.summary.no_progress_abort"
        assert event.level == "ERROR"

    def test_emit_incremental_no_progress_abort(self):
        event = emit_incremental_no_progress_abort("r1", attempts=1)
        assert event.type == "execution.summary.incremental_no_progress_abort"

    def test_emit_overflow_chunking(self):
        event = emit_overflow_chunking("r1", chunks=4)
        assert event.type == "execution.summary.overflow_chunking"

    def test_emit_incremental_overflow_chunking(self):
        event = emit_incremental_overflow_chunking("r1", chunks=3)
        assert event.type == "execution.summary.incremental_overflow_chunking"

    def test_emit_overflow_forced_split(self):
        event = emit_overflow_forced_split("r1", chunks=4)
        assert event.type == "execution.summary.overflow_forced_split"

    def test_emit_incremental_overflow_forced_split(self):
        event = emit_incremental_overflow_forced_split("r1", chunks=3)
        assert event.type == "execution.summary.incremental_overflow_forced_split"

    def test_emit_http_retry_chunking(self):
        event = emit_http_retry_chunking("r1", chunks=2, status=503)
        assert event.type == "execution.summary.http_retry_chunking"

    def test_emit_incremental_http_retry_chunking(self):
        event = emit_incremental_http_retry_chunking("r1", chunks=2, status=503)
        assert event.type == "execution.summary.incremental_http_retry_chunking"

    def test_emit_http_retry_forced_split(self):
        event = emit_http_retry_forced_split("r1", chunks=2)
        assert event.type == "execution.summary.http_retry_forced_split"

    def test_emit_incremental_http_retry_forced_split(self):
        event = emit_incremental_http_retry_forced_split("r1", chunks=2)
        assert event.type == "execution.summary.incremental_http_retry_forced_split"


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_summary_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        emit_plan(dispatcher=dispatcher)
        emit_request("r1", dispatcher=dispatcher)
        emit_reply("r1", dispatcher=dispatcher)
        emit_retry_exhausted("r1", dispatcher=dispatcher)
        emit_incremental_retry_exhausted("r1", dispatcher=dispatcher)
        emit_preflight_chunking("r1", dispatcher=dispatcher)
        emit_incremental_preflight_chunking("r1", dispatcher=dispatcher)
        emit_preflight_forced_split("r1", dispatcher=dispatcher)
        emit_incremental_preflight_forced_split("r1", dispatcher=dispatcher)
        emit_no_progress_abort("r1", dispatcher=dispatcher)
        emit_incremental_no_progress_abort("r1", dispatcher=dispatcher)
        emit_overflow_chunking("r1", dispatcher=dispatcher)
        emit_incremental_overflow_chunking("r1", dispatcher=dispatcher)
        emit_overflow_forced_split("r1", dispatcher=dispatcher)
        emit_incremental_overflow_forced_split("r1", dispatcher=dispatcher)
        emit_http_retry_chunking("r1", dispatcher=dispatcher)
        emit_incremental_http_retry_chunking("r1", dispatcher=dispatcher)
        emit_http_retry_forced_split("r1", dispatcher=dispatcher)
        emit_incremental_http_retry_forced_split("r1", dispatcher=dispatcher)

        assert len(received) == 19
