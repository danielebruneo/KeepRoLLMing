"""Tests for default projectors (D-072 §6, Phase P2).

Verifies:
- structured projector: JsonFormatter, TRACE level, single FileSink
- main projector: PlainTextFormatter, BASIC level, two sinks (StdoutSink + FileSink)
- server projector: CompactFormatter, INFO level, single FileSink
- all projectors use empty selector
- activation subscribes to root namespaces
- events are filtered correctly by level for each projector
"""

import os
import tempfile
from typing import Any, Dict, List

import pytest

from keeprollming.observability.default_projectors import (
    activate_default_projectors,
    create_default_projectors,
    deactivate_default_projectors,
)
from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.formatters import (
    CompactFormatter,
    JsonFormatter,
    PlainTextFormatter,
)
from keeprollming.observability.projectors import FileSink, RotatingFileSink, StdoutSink


@pytest.fixture
def temp_log_dir():
    """Provide a temporary directory for log files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def default_projectors(temp_log_dir):
    """Create default projectors with a temp log directory."""
    return create_default_projectors(log_dir=temp_log_dir)


class TestStructuredProjector:
    """Tests for the structured projector configuration."""

    def test_structured_is_first(self, default_projectors):
        """structured projector is returned first."""
        assert default_projectors[0].name == "structured"

    def test_structured_uses_json_formatter(self, default_projectors):
        """structured projector uses JsonFormatter."""
        assert isinstance(default_projectors[0].formatter, JsonFormatter)

    def test_structured_level_is_info_by_default(self, default_projectors):
        """Structured logs are bounded operational records by default."""
        assert default_projectors[0].level == "INFO"

    def test_structured_empty_selector(self, default_projectors):
        """structured projector uses empty selector (all events)."""
        assert default_projectors[0].selector == ""

    def test_structured_single_file_sink(self, default_projectors):
        """structured projector has a single FileSink."""
        sinks = default_projectors[0].sinks
        assert len(sinks) == 1
        assert isinstance(sinks[0], FileSink)

    def test_structured_sink_path(self, default_projectors, temp_log_dir):
        """structured projector writes to keeprollming.log.json."""
        sink = default_projectors[0].sinks[0]
        assert sink._path == os.path.join(temp_log_dir, "keeprollming.log.json")

    def test_projector_configuration_overrides_defaults(self, temp_log_dir):
        projectors = create_default_projectors(temp_log_dir, {
            "json": {"enabled": False},
            "plain": {"level": "DEBUG", "stdout": False, "path": "debug.log"},
            "server": {"enabled": False},
        })
        assert len(projectors) == 1
        assert projectors[0].name == "main"
        assert projectors[0].level == "DEBUG"
        assert len(projectors[0].sinks) == 1
        assert projectors[0].sinks[0]._path == os.path.join(temp_log_dir, "debug.log")


def test_rotating_file_sink_retains_bounded_backups(tmp_path):
    sink = RotatingFileSink(str(tmp_path / "events.log"), max_bytes=6, backup_count=1)
    sink.write("first")
    sink.write("second")
    assert (tmp_path / "events.log.1").read_text() == "first\n"
    assert (tmp_path / "events.log").read_text() == "second\n"

    def test_structured_captures_operational_levels_by_default(self, default_projectors, temp_log_dir):
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        # Emit events at various levels using execution.* namespace
        # (projectors subscribe to root namespaces: execution, request, etc.)
        for level in ("TRACE", "DEBUG", "INFO", "BASIC", "WARN", "ERROR"):
            dispatcher.emit(
                RuntimeEvent(
                    type="execution.test.level",
                    source=EventSource(domain="execution", component="test"),
                    data={"level": level},
                    level=level,
                )
            )

        # Read the structured log file
        log_path = os.path.join(temp_log_dir, "keeprollming.log.json")
        assert os.path.exists(log_path)
        with open(log_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]

        assert len(lines) == 4  # INFO, BASIC, WARN, ERROR


class TestMainProjector:
    """Tests for the main projector configuration."""

    def test_main_is_second(self, default_projectors):
        """main projector is returned second."""
        assert default_projectors[1].name == "main"

    def test_main_uses_plaintext_formatter(self, default_projectors):
        """main projector uses PlainTextFormatter."""
        assert isinstance(default_projectors[1].formatter, PlainTextFormatter)

    def test_main_level_is_basic(self, default_projectors):
        """main projector has BASIC level."""
        assert default_projectors[1].level == "BASIC"

    def test_main_empty_selector(self, default_projectors):
        """main projector uses empty selector (all events)."""
        assert default_projectors[1].selector == ""

    def test_main_has_two_sinks(self, default_projectors):
        """main projector has two sinks: StdoutSink and FileSink."""
        sinks = default_projectors[1].sinks
        assert len(sinks) == 2

    def test_main_first_sink_is_stdout(self, default_projectors):
        """main projector's first sink is StdoutSink."""
        assert isinstance(default_projectors[1].sinks[0], StdoutSink)

    def test_main_second_sink_is_file(self, default_projectors):
        """main projector's second sink is FileSink."""
        assert isinstance(default_projectors[1].sinks[1], FileSink)

    def test_main_sink_path(self, default_projectors, temp_log_dir):
        """main projector writes to keeprollming.log."""
        file_sink = default_projectors[1].sinks[1]
        assert file_sink._path == os.path.join(temp_log_dir, "keeprollming.log")

    def test_main_filters_by_basic_level(self, default_projectors, temp_log_dir):
        """main projector filters events at BASIC level and above."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        # Emit events below BASIC (should be filtered)
        for level in ("TRACE", "DEBUG", "INFO"):
            dispatcher.emit(
                RuntimeEvent(
                    type="execution.test.main.level",
                    source=EventSource(domain="execution", component="test"),
                    data={"level": level},
                    level=level,
                )
            )

        # Emit events at/above BASIC (should be captured)
        for level in ("BASIC", "WARN", "ERROR"):
            dispatcher.emit(
                RuntimeEvent(
                    type="execution.test.main.level",
                    source=EventSource(domain="execution", component="test"),
                    data={"level": level},
                    level=level,
                )
            )

        # Read the main log file
        log_path = os.path.join(temp_log_dir, "keeprollming.log")
        assert os.path.exists(log_path)
        with open(log_path, "r") as f:
            content = f.read()

        # Should contain events at/above BASIC level (check event type presence)
        lines = [l for l in content.strip().split("\n") if l.strip()]
        assert len(lines) == 3  # BASIC, WARN, ERROR events only
        # All captured lines should be execution.test.main.level events
        for line in lines:
            assert "execution.test.main.level" in line


class TestServerProjector:
    """Tests for the server projector configuration."""

    def test_server_is_third(self, default_projectors):
        """server projector is returned third."""
        assert default_projectors[2].name == "server"

    def test_server_uses_compact_formatter(self, default_projectors):
        """server projector uses CompactFormatter."""
        assert isinstance(default_projectors[2].formatter, CompactFormatter)

    def test_server_level_is_info(self, default_projectors):
        """server projector has INFO level."""
        assert default_projectors[2].level == "INFO"

    def test_server_selects_terminal_request_summary(self, default_projectors):
        """server projector is an access log, not a generic INFO sink."""
        assert default_projectors[2].selector == "execution.performance.request_complete"

    def test_server_single_file_sink(self, default_projectors):
        """server projector has a single FileSink."""
        sinks = default_projectors[2].sinks
        assert len(sinks) == 1
        assert isinstance(sinks[0], FileSink)

    def test_server_sink_path(self, default_projectors, temp_log_dir):
        """server projector writes to server.log."""
        sink = default_projectors[2].sinks[0]
        assert sink._path == os.path.join(temp_log_dir, "server.log")

    def test_server_emits_only_terminal_request_summary(self, default_projectors, temp_log_dir):
        """Internal INFO events do not pollute the compact access log."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        # Emit events below INFO (should be filtered)
        for level in ("TRACE", "DEBUG"):
            dispatcher.emit(
                RuntimeEvent(
                    type="execution.test.server.level",
                    source=EventSource(domain="execution", component="test"),
                    data={"level": level},
                    level=level,
                )
            )

        # Internal events of any level must be rejected by the selector.
        for level in ("INFO", "BASIC", "WARN", "ERROR"):
            dispatcher.emit(
                RuntimeEvent(
                    type="execution.test.server.level",
                    source=EventSource(domain="execution", component="test"),
                    data={"level": level},
                    level=level,
                )
            )

        # A terminal request summary is the only eligible event.
        dispatcher.emit(RuntimeEvent(
            type="execution.performance.request_complete",
            source=EventSource(domain="execution", component="performance"),
            data={"route_name": "chat/main", "model": "test", "elapsed_ms": 12},
            level="BASIC", req_id="request-1",
        ))
        log_path = os.path.join(temp_log_dir, "server.log")
        assert os.path.exists(log_path)
        with open(log_path, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]

        assert len(lines) == 1
        assert "req_id=request-1" in lines[0]


class TestActivation:
    """Tests for projector activation and subscription."""

    def test_activate_subscribes_to_dispatcher(self, default_projectors):
        """activate_default_projectors subscribes all projectors."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        for projector in default_projectors:
            assert projector.active is True

    def test_activate_root_namespace_subscriptions(self, default_projectors):
        """Projectors subscribe to root namespaces via activate()."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        # Each projector subscribes to its name + root namespaces
        # Check that consumers are registered
        for ns in ("execution", "request", "streaming", "routing", "downstream", "filter"):
            assert ns in dispatcher._consumers

    def test_deactivate_unsubscribes(self, default_projectors):
        """deactivate_default_projectors unsubscribes all projectors."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        deactivate_default_projectors(default_projectors)

        for projector in default_projectors:
            assert projector.active is False

    def test_events_flow_to_all_projectors(self, default_projectors, temp_log_dir):
        """Events are dispatched to all active projectors."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        # A generic BASIC event belongs only in structured + main projections.
        dispatcher.emit(
            RuntimeEvent(
                type="execution.test.event",
                source=EventSource(domain="execution", component="test"),
                data={"message": "test"},
                level="BASIC",
            )
        )

        # Check all log files have content
        structured_path = os.path.join(temp_log_dir, "keeprollming.log.json")
        main_path = os.path.join(temp_log_dir, "keeprollming.log")
        server_path = os.path.join(temp_log_dir, "server.log")

        assert os.path.exists(structured_path)
        assert os.path.exists(main_path)
        assert not os.path.exists(server_path)

        with open(structured_path, "r") as f:
            assert len(f.readlines()) > 0
        with open(main_path, "r") as f:
            assert len(f.readlines()) > 0


class TestLevelFilteringPerProjector:
    """Tests verifying correct level filtering per projector."""

    def test_trace_event_is_not_persisted_by_default(self, default_projectors, temp_log_dir):
        """High-volume TRACE data requires explicit opt-in."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        dispatcher.emit(
            RuntimeEvent(
                type="execution.test.level.trace",
                source=EventSource(domain="execution", component="test"),
                data={"test": "trace"},
                level="TRACE",
            )
        )

        structured_path = os.path.join(temp_log_dir, "keeprollming.log.json")
        if os.path.exists(structured_path):
            with open(structured_path, "r") as f:
                assert not any("execution.test.level.trace" in line for line in f.readlines())

        # main (BASIC) should NOT have it — file may not exist if no events matched
        main_path = os.path.join(temp_log_dir, "keeprollming.log")
        if os.path.exists(main_path):
            with open(main_path, "r") as f:
                assert not any("execution.test.level.trace" in line for line in f.readlines())

        # server (INFO) should NOT have it — file may not exist if no events matched
        server_path = os.path.join(temp_log_dir, "server.log")
        if os.path.exists(server_path):
            with open(server_path, "r") as f:
                assert not any("execution.test.level.trace" in line for line in f.readlines())

    def test_debug_event_is_not_persisted_by_default(self, default_projectors, temp_log_dir):
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        dispatcher.emit(
            RuntimeEvent(
                type="execution.test.level.debug",
                source=EventSource(domain="execution", component="test"),
                data={"test": "debug"},
                level="DEBUG",
            )
        )

        structured_path = os.path.join(temp_log_dir, "keeprollming.log.json")
        if os.path.exists(structured_path):
            with open(structured_path, "r") as f:
                assert not any("execution.test.level.debug" in line for line in f.readlines())

        # main (BASIC) should NOT have it — file may not exist if no events matched
        main_path = os.path.join(temp_log_dir, "keeprollming.log")
        if os.path.exists(main_path):
            with open(main_path, "r") as f:
                assert not any("execution.test.level.debug" in line for line in f.readlines())

        # server (INFO) should NOT have it — file may not exist if no events matched
        server_path = os.path.join(temp_log_dir, "server.log")
        if os.path.exists(server_path):
            with open(server_path, "r") as f:
                assert not any("execution.test.level.debug" in line for line in f.readlines())

    def test_info_event_visible_only_to_structured(self, default_projectors, temp_log_dir):
        """Generic INFO events are not access-log records."""
        dispatcher = EventDispatcher()
        activate_default_projectors(default_projectors, dispatcher)

        dispatcher.emit(
            RuntimeEvent(
                type="execution.test.level.info",
                source=EventSource(domain="execution", component="test"),
                data={"test": "info"},
                level="INFO",
            )
        )

        # structured (TRACE) should have it
        with open(os.path.join(temp_log_dir, "keeprollming.log.json"), "r") as f:
            assert any("execution.test.level.info" in line for line in f.readlines())

        # main (BASIC) should NOT have it (INFO < BASIC) — file may not exist if no events matched
        main_path = os.path.join(temp_log_dir, "keeprollming.log")
        if os.path.exists(main_path):
            with open(main_path, "r") as f:
                assert not any("execution.test.level.info" in line for line in f.readlines())

        assert not os.path.exists(os.path.join(temp_log_dir, "server.log"))


class TestArchitectureCompliance:
    """Tests verifying D-072 invariant compliance."""

    def test_i_d072_01_format_level_orthogonal(self, default_projectors):
        """I-D072-01: FORMAT and LEVEL are orthogonal.

        Each projector independently chooses its format and level;
        no formatter enforces its own event whitelist or level mapping.
        """
        # structured: JSONL + TRACE
        assert isinstance(default_projectors[0].formatter, JsonFormatter)
        assert default_projectors[0].level == "INFO"

        # main: PLAIN + BASIC
        assert isinstance(default_projectors[1].formatter, PlainTextFormatter)
        assert default_projectors[1].level == "BASIC"

        # server: HTTP + INFO
        assert isinstance(default_projectors[2].formatter, CompactFormatter)
        assert default_projectors[2].level == "INFO"

        # Different formats at different levels proves orthogonality
        # No formatter constrains the level choice

    def test_i_d073_01_projector_is_configuration_not_consumer(self, default_projectors):
        """I-D073-01: Projector is a configuration-driven projection, not a consumer type.

        Projectors use the existing EventDispatcher subscription model;
        they are not new consumer types but configured projections.
        """
        # All projectors are instances of the same Projector class
        # with different configurations (selector, level, formatter, sinks)
        for projector in default_projectors:
            assert hasattr(projector, "selector")
            assert hasattr(projector, "level")
            assert hasattr(projector, "formatter")
            assert hasattr(projector, "sinks")
