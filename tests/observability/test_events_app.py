"""Tests for app event emission helpers (O7 Phase 3 migration).

Verifies that ``events_app`` creates correct RuntimeEvent envelopes.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_app import (
    emit_app_event,
    emit_perf_logs_dir,
    emit_config_reloaded,
    emit_config_reload_failed,
    emit_starting,
    emit_stopping,
    emit_not_found,
)


class TestEmitAppEvent:
    """Test the core emit_app_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_app_event("", "execution.app.test")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.app.test"
        assert event.source == EventSource(domain="execution", component="app")

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", emitted_events.append)
        event = emit_app_event("", "execution.app.test", dispatcher=dispatcher)
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_perf_logs_dir(self):
        event = emit_perf_logs_dir(message="/tmp/logs")
        assert event.type == "execution.app.perf_logs_dir"

    def test_emit_config_reloaded(self):
        event = emit_config_reloaded(message="reloading...", config_mtime=123.0)
        assert event.type == "execution.app.config_reloaded"
        assert event.data["config_mtime"] == 123.0

    def test_emit_config_reload_failed(self):
        event = emit_config_reload_failed(error="invalid configuration")
        assert event.type == "execution.app.config_reload_failed"
        assert event.level == "ERROR"

    def test_emit_starting(self):
        event = emit_starting(message="Starting...")
        assert event.type == "execution.app.starting"

    def test_emit_stopping(self):
        event = emit_stopping(message="Shutting down...")
        assert event.type == "execution.app.stopping"

    def test_emit_not_found(self):
        event = emit_not_found(path="/v1/chat/completions")
        assert event.type == "execution.app.not_found"
        assert event.level == "WARN"


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_app_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        emit_perf_logs_dir(dispatcher=dispatcher)
        emit_config_reloaded(dispatcher=dispatcher)
        emit_config_reload_failed(error="invalid configuration", dispatcher=dispatcher)
        emit_starting(dispatcher=dispatcher)
        emit_stopping(dispatcher=dispatcher)
        emit_not_found(path="/test", dispatcher=dispatcher)

        assert len(received) == 6
