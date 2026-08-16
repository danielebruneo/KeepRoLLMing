"""Unit tests for Projector model (D-072, Phase P1)."""

import json
import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events import EventSource, RuntimeEvent, LEVEL_ORDER
from keeprollming.observability.formatters import JsonFormatter, PlainTextFormatter
from keeprollming.observability.projectors import (
    FileSink,
    Projector,
    Sink,
    StdoutSink,
)


def _make_event(
    event_type: str = "streaming.parser.event",
    data: dict | None = None,
    req_id: str | None = "test-req",
    level: str = "INFO",
) -> RuntimeEvent:
    """Helper to create a minimal RuntimeEvent for testing."""
    return RuntimeEvent(
        type=event_type,
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="streaming", component="parser"),
        data=data or {"key": "value"},
        req_id=req_id,
        level=level,
    )


class TestProjectorInitialization:
    """Test Projector configuration and validation."""

    def test_projector_minimal_config(self):
        """Projector can be created with just a name."""
        p = Projector(name="test")
        assert p.name == "test"
        assert p.selector == ""
        assert p.level == "INFO"
        assert isinstance(p.formatter, JsonFormatter)
        assert p.sinks == []
        assert not p.active

    def test_projector_full_config(self):
        """Projector accepts all configuration fields."""
        formatter = PlainTextFormatter()
        sink = StdoutSink()
        p = Projector(
            name="full",
            selector="streaming.*",
            level="DEBUG",
            formatter=formatter,
            sinks=[sink],
        )
        assert p.name == "full"
        assert p.selector == "streaming.*"
        assert p.level == "DEBUG"
        assert p.formatter is formatter
        assert p.sinks == [sink]

    def test_projector_invalid_level(self):
        """Projector rejects invalid level in __post_init__."""
        with pytest.raises(ValueError, match="must be one of"):
            Projector(name="bad", level="INVALID")

    def test_projector_valid_levels(self):
        """Projector accepts all valid levels from LEVEL_ORDER."""
        for level in LEVEL_ORDER:
            p = Projector(name=f"level-{level}", level=level)
            assert p.level == level


class TestProjectorSelectorMatch:
    """Test projector selector (event type) matching."""

    def test_empty_selector_matches_all(self):
        """Empty selector matches all event types."""
        p = Projector(name="all", selector="", level="TRACE")
        for et in ("streaming.parser.event", "execution.request.start", "filter.chain.executed"):
            assert p._matches_selector(et), f"Expected {et!r} to match empty selector"

    def test_prefix_selector_match(self):
        """Prefix glob matches events under that namespace."""
        p = Projector(name="streaming-only", selector="streaming.*", level="TRACE")
        assert p._matches_selector("streaming.parser.event") is True
        assert p._matches_selector("streaming.runner.start") is True
        assert p._matches_selector("execution.request.start") is False

    def test_exact_selector_match(self):
        """Exact event type match."""
        p = Projector(name="exact", selector="streaming.parser.event", level="TRACE")
        assert p._matches_selector("streaming.parser.event") is True
        assert p._matches_selector("streaming.runner.start") is False

    def test_wildcard_selector(self):
        """Wildcard patterns work via fnmatch."""
        p = Projector(name="wildcard", selector="*.parser.*", level="TRACE")
        assert p._matches_selector("streaming.parser.event") is True
        assert p._matches_selector("filter.parser.error") is True
        assert p._matches_selector("execution.request.start") is False


class TestProjectorLevelFiltering:
    """Test projector level filtering."""

    def test_level_filtering_basic(self):
        """Events below minimum level are dropped; at/above pass."""
        # Level hierarchy: TRACE < DEBUG < INFO < BASIC < WARN < ERROR
        p = Projector(name="info-up", selector="", level="INFO")

        assert p._should_emit(_make_event(level="TRACE")) is False
        assert p._should_emit(_make_event(level="DEBUG")) is False
        assert p._should_emit(_make_event(level="INFO")) is True
        assert p._should_emit(_make_event(level="BASIC")) is True
        assert p._should_emit(_make_event(level="WARN")) is True
        assert p._should_emit(_make_event(level="ERROR")) is True

    def test_level_trace_captures_all(self):
        """TRACE level captures all events."""
        p = Projector(name="trace", selector="", level="TRACE")
        for lvl in LEVEL_ORDER:
            assert p._should_emit(_make_event(level=lvl)), f"TRACE should capture {lvl}"

    def test_level_error_only(self):
        """ERROR level captures only ERROR events."""
        p = Projector(name="errors", selector="", level="ERROR")
        for lvl in LEVEL_ORDER:
            expected = lvl == "ERROR"
            assert p._should_emit(_make_event(level=lvl)) is expected, f"ERROR filter for {lvl}"

    def test_selector_and_level_conjunctive(self):
        """Both selector AND level must match."""
        p = Projector(name="streaming-info", selector="streaming.*", level="INFO")

        # Matches selector, meets level
        assert p._should_emit(_make_event(event_type="streaming.parser.event", level="INFO")) is True

        # Matches selector, below level
        assert p._should_emit(_make_event(event_type="streaming.parser.event", level="DEBUG")) is False

        # Doesn't match selector, meets level
        assert p._should_emit(_make_event(event_type="execution.request.start", level="ERROR")) is False


class TestProjectorSubscriptionActivate:
    """Test projector subscription mechanics with EventDispatcher."""

    def test_activate_subscribes_to_dispatcher(self):
        """activate() subscribes projector to dispatcher and sets active=True."""
        dispatcher = EventDispatcher()
        p = Projector(name="test", selector="", level="TRACE", sinks=[StdoutSink()])

        assert not p.active
        p.activate(dispatcher)
        assert p.active is True

    def test_activate_receives_events(self):
        """Activated projector receives and processes events from dispatcher."""
        dispatcher = EventDispatcher()
        captured_output: list[str] = []

        class CaptureSink(Sink):
            def write(self, text: str) -> None:
                captured_output.append(text)

        p = Projector(
            name="capture",
            selector="",
            level="TRACE",
            sinks=[CaptureSink()],
        )
        p.activate(dispatcher)

        event = _make_event(level="INFO")
        dispatcher.emit(event)

        assert len(captured_output) == 1
        # Verify it's formatted JSON from JsonFormatter
        parsed = json.loads(captured_output[0])
        assert parsed["type"] == "streaming.parser.event"

    def test_activate_filters_non_matching(self):
        """Activated projector drops events that don't match selector/level."""
        dispatcher = EventDispatcher()
        captured_output: list[str] = []

        class CaptureSink(Sink):
            def write(self, text: str) -> None:
                captured_output.append(text)

        p = Projector(
            name="filtered",
            selector="streaming.*",
            level="WARN",
            sinks=[CaptureSink()],
        )
        p.activate(dispatcher)

        # Should pass: matches selector, meets level
        dispatcher.emit(_make_event(event_type="streaming.parser.event", level="WARN"))
        # Should drop: below level
        dispatcher.emit(_make_event(event_type="streaming.parser.event", level="INFO"))
        # Should drop: doesn't match selector
        dispatcher.emit(_make_event(event_type="execution.request.start", level="ERROR"))

        assert len(captured_output) == 1

    def test_deactivate_stops_receiving_events(self):
        """deactivate() stops projector from receiving events."""
        dispatcher = EventDispatcher()
        captured_output: list[str] = []

        class CaptureSink(Sink):
            def write(self, text: str) -> None:
                captured_output.append(text)

        p = Projector(name="toggle", selector="", level="TRACE", sinks=[CaptureSink()])
        p.activate(dispatcher)

        dispatcher.emit(_make_event(level="INFO"))
        assert len(captured_output) == 1

        p.deactivate()
        assert not p.active

        dispatcher.emit(_make_event(level="INFO"))
        assert len(captured_output) == 1  # No new events after deactivate


class TestProjectorFormatAndSink:
    """Test projector formatting and sink delivery."""

    def test_projector_uses_configured_formatter(self):
        """Projector formats events with its configured formatter."""
        dispatcher = EventDispatcher()
        captured_output: list[str] = []

        class CaptureSink(Sink):
            def write(self, text: str) -> None:
                captured_output.append(text)

        p = Projector(
            name="plain",
            selector="",
            level="TRACE",
            formatter=PlainTextFormatter(),
            sinks=[CaptureSink()],
        )
        p.activate(dispatcher)

        event = _make_event(level="DEBUG", data={"test": True})
        dispatcher.emit(event)

        assert len(captured_output) == 1
        # PlainTextFormatter produces human-readable output with event type and data
        assert "streaming.event" in captured_output[0]
        assert "test=True" in captured_output[0]

    def test_projector_writes_to_multiple_sinks(self):
        """Projector writes formatted output to all configured sinks."""
        dispatcher = EventDispatcher()
        sink1_output: list[str] = []
        sink2_output: list[str] = []

        class CaptureSink(Sink):
            def __init__(self, target: list[str]):
                self._target = target

            def write(self, text: str) -> None:
                self._target.append(text)

        p = Projector(
            name="multi",
            selector="",
            level="TRACE",
            sinks=[CaptureSink(sink1_output), CaptureSink(sink2_output)],
        )
        p.activate(dispatcher)

        dispatcher.emit(_make_event(level="INFO"))

        assert len(sink1_output) == 1
        assert len(sink2_output) == 1
        assert sink1_output[0] == sink2_output[0]


class TestFileSink:
    """Test FileSink implementation."""

    def test_file_sink_write(self, tmp_path):
        """FileSink writes formatted text to specified path."""
        file_path = tmp_path / "test.log"
        sink = FileSink(str(file_path))

        sink.write("line one")
        sink.write("line two")

        content = file_path.read_text()
        assert "line one\n" in content
        assert "line two\n" in content

    def test_file_sink_creates_parent_dirs(self, tmp_path):
        """FileSink creates parent directories if needed."""
        file_path = tmp_path / "subdir" / "nested" / "test.log"
        sink = FileSink(str(file_path))

        sink.write("test")

        assert file_path.exists()
        assert file_path.read_text().strip() == "test"

    def test_file_sink_appends(self, tmp_path):
        """FileSink appends to existing file."""
        file_path = tmp_path / "test.log"
        file_path.write_text("existing\n")

        sink = FileSink(str(file_path))
        sink.write("new line")

        content = file_path.read_text()
        assert content == "existing\nnew line\n"


class TestStdoutSink:
    """Test StdoutSink implementation."""

    def test_stdout_sink_print(self, capsys):
        """StdoutSink prints output to stdout."""
        sink = StdoutSink()
        sink.write("hello stdout")

        captured = capsys.readouterr()
        assert "hello stdout" in captured.out
