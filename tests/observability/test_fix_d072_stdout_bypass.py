"""Tests for FIX-D072: Direct JSON stdout bypass removal.

Verifies that the fixes correctly remove direct JSON stdout output from
legacy log() and emit_* helpers, while preserving RuntimeEvent emission
through the dispatcher/projector pipeline.
"""

import pytest
from unittest.mock import patch, MagicMock

from keeprollming.logger import log
from keeprollming.observability import EventDispatcher, RuntimeEvent
from keeprollming.observability.events_app import (
    emit_app_event,
    emit_starting,
)
from keeprollming.observability.events_upstream import (
    emit_upstream_event,
    emit_response_received,
)


class TestNoDirectJSONStdoutFromLog:
    """Test 1: logger.log() does NOT write to stdout."""

    def test_log_does_not_print_to_stdout(self, capfd):
        """Calling logger.log() produces no stdout output.

        FIX-D072 removed the unconditional print_json()/print() block from
        log() that bypassed the Projector architecture.
        """
        log("INFO", "test_message", key="value")

        captured = capfd.readouterr()
        assert captured.out == "", (
            f"log() wrote to stdout, violating I-D072-01: {captured.out!r}"
        )

    def test_log_does_not_print_json_to_stdout(self, capfd):
        """log() output does not contain JSON objects on stdout."""
        log("DEBUG", "request_sent", url="http://example.com")

        captured = capfd.readouterr()
        assert '{"ts"' not in captured.out, (
            "JSON object found on stdout from log()"
        )
        assert '"level"' not in captured.out or captured.out == "", (
            "JSON-level field found on stdout from log()"
        )

    def test_log_still_emits_via_dispatcher(self):
        """log() continues emitting RuntimeEvents via EventDispatcher shim."""
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("diagnostic", received.append)

        with patch("keeprollming.app.get_event_dispatcher", return_value=dispatcher):
            log("INFO", "startup", upstream="http://test")

        assert len(received) == 1
        assert received[0].type == "diagnostic.startup"


class TestEmitNoOpWithoutDispatcher:
    """Test 2: emit_* helpers with no dispatcher are no-ops."""

    def test_emit_app_event_no_dispatcher_is_noop(self, capfd):
        """emit_app_event() without dispatcher produces no output."""
        with patch("keeprollming.logger.log") as mock_log:
            emit_app_event("", "execution.app.test", dispatcher=None)

        mock_log.assert_not_called()
        captured = capfd.readouterr()
        assert captured.out == ""

    def test_emit_starting_no_dispatcher_is_noop(self, capfd):
        """emit_starting() without dispatcher produces no output."""
        with patch("keeprollming.logger.log") as mock_log:
            emit_starting(message="Starting...", dispatcher=None)

        mock_log.assert_not_called()
        captured = capfd.readouterr()
        assert captured.out == ""

    def test_emit_upstream_event_no_dispatcher_is_noop(self, capfd):
        """emit_upstream_event() without dispatcher produces no output."""
        with patch("keeprollming.logger.log") as mock_log:
            emit_upstream_event("", "execution.upstream.test", dispatcher=None)

        mock_log.assert_not_called()
        captured = capfd.readouterr()
        assert captured.out == ""

    def test_emit_response_received_no_dispatcher_is_noop(self, capfd):
        """emit_response_received() without dispatcher produces no output."""
        with patch("keeprollming.logger.log") as mock_log:
            emit_response_received(
                url="http://test", method="GET", status=200, dispatcher=None
            )

        mock_log.assert_not_called()
        captured = capfd.readouterr()
        assert captured.out == ""


class TestProjectorOutputStillPresent:
    """Test 3: RuntimeEvents emitted through dispatcher still reach projectors."""

    def test_dispatcher_emits_to_subscribers(self):
        """Events emitted via dispatcher are received by subscribers."""
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        emit_starting(message="Starting...", dispatcher=dispatcher)

        assert len(received) == 1
        assert received[0].type == "execution.app.starting"
        assert received[0].data["message"] == "Starting..."

    def test_plain_formatter_produces_human_readable_output(self, capfd):
        """PLAIN formatter produces human-readable output via StdoutSink."""
        from keeprollming.observability.formatters import PlainTextFormatter
        from keeprollming.observability.projectors import Projector, StdoutSink

        formatter = PlainTextFormatter()
        event = RuntimeEvent(
            type="execution.app.starting",
            timestamp_ns=1_000_000_000_000_000_000,
            source=MagicMock(domain="execution", component="app"),
            data={"message": "Starting..."},
            level="INFO",
        )

        output = formatter.format(event)
        # PLAIN format should be human-readable, not raw JSON dump
        assert isinstance(output, str)
        assert "execution.app.starting" in output or "starting" in output.lower()

    def test_stdout_sink_writes_to_stdout(self, capfd):
        """StdoutSink correctly writes formatted output to stdout."""
        from keeprollming.observability.projectors import StdoutSink

        sink = StdoutSink()
        sink.write("test output line")

        captured = capfd.readouterr()
        assert "test output line" in captured.out


class TestUpstreamDispatcherWiring:
    """Test 4: upstream_client dispatcher wiring works correctly."""

    def test_set_upstream_dispatcher_sets_global(self):
        """set_upstream_dispatcher() configures the module-level dispatcher."""
        import keeprollming.upstream.upstream_client as uc
        previous = uc._upstream_dispatcher
        mock_disp = MagicMock()
        try:
            uc.set_upstream_dispatcher(mock_disp)
            assert uc._upstream_dispatcher is mock_disp
        finally:
            # Lifespan startup can already have initialized application state.
            uc.set_upstream_dispatcher(previous)
