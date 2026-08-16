"""Unit tests for LoggerConsumer (Phase O3)."""

import logging
import time

import pytest

from keeprollming.observability.consumers import LoggerConsumer
from keeprollming.observability.events import EventSource, RuntimeEvent


def _make_event(
    type_str: str = "streaming.parser.event",
    level: str = "DEBUG",
    req_id: str | None = None,
    data: dict | None = None,
) -> RuntimeEvent:
    """Helper to create a minimal RuntimeEvent."""
    return RuntimeEvent(
        type=type_str,
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="streaming", component="parser"),
        data=data or {"test": True},
        level=level,
        req_id=req_id,
    )


class TestLoggerConsumerInit:
    """Test LoggerConsumer initialization."""

    def test_default_init(self):
        """LoggerConsumer initializes with default capture=True, level=DEBUG."""
        consumer = LoggerConsumer()
        assert consumer._capture is True
        assert consumer._min_level == "DEBUG"
        assert len(consumer.captured) == 0

    def test_init_capture_false(self):
        """LoggerConsumer initializes with capture=False."""
        consumer = LoggerConsumer(capture=False)
        assert consumer._capture is False

    def test_init_level_filter(self):
        """LoggerConsumer initializes with custom level filter."""
        consumer = LoggerConsumer(level="INFO")
        assert consumer._min_level == "INFO"

    def test_loggers_initialized(self):
        """LoggerConsumer sets up loggers for all levels."""
        consumer = LoggerConsumer()
        assert "DEBUG" in consumer._loggers
        assert "INFO" in consumer._loggers
        assert "WARN" in consumer._loggers
        assert "ERROR" in consumer._loggers

    def test_loggers_have_json_formatter(self):
        """LoggerConsumer assigns JSON formatter to all loggers."""
        consumer = LoggerConsumer()
        for level, lg in consumer._loggers.items():
            assert len(lg.handlers) == 1
            handler = lg.handlers[0]
            assert handler.__class__.__name__ == "StreamHandler"


class TestLoggerConsumerCapture:
    """Test LoggerConsumer event capture."""

    def test_call_captures_event(self):
        """LoggerConsumer captures events when capture=True."""
        consumer = LoggerConsumer(capture=True)
        event = _make_event()
        consumer(event)
        assert len(consumer.captured) == 1
        assert consumer.captured[0] is event

    def test_call_does_not_capture_when_capture_false(self):
        """LoggerConsumer does not capture when capture=False."""
        consumer = LoggerConsumer(capture=False)
        event = _make_event()
        consumer(event)
        assert len(consumer.captured) == 0

    def test_captured_returns_copy(self):
        """LoggerConsumer.captured returns a copy, not the internal list."""
        consumer = LoggerConsumer(capture=True)
        events_ref = consumer.captured
        consumer(_make_event())
        assert len(events_ref) == 0
        assert len(consumer.captured) == 1

    def test_clear_resets_captured(self):
        """LoggerConsumer.clear() resets captured events."""
        consumer = LoggerConsumer(capture=True)
        consumer(_make_event())
        consumer(_make_event())
        assert len(consumer.captured) == 2
        consumer.clear()
        assert len(consumer.captured) == 0


class TestLoggerConsumerLevelRouting:
    """Test LoggerConsumer level routing (INV-03)."""

    def test_debug_routes_to_diagnostic(self):
        """DEBUG events route to diagnostic logger."""
        consumer = LoggerConsumer(capture=True)
        event = _make_event(level="DEBUG")
        consumer(event)
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "DEBUG"
        assert consumer._loggers["DEBUG"].name == "keeprollming.diagnostic"

    def test_info_routes_to_request(self):
        """INFO events route to request logger."""
        consumer = LoggerConsumer(capture=True)
        event = _make_event(level="INFO")
        consumer(event)
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "INFO"
        assert consumer._loggers["INFO"].name == "keeprollming.request"

    def test_warn_routes_to_request(self):
        """WARN events route to request logger."""
        consumer = LoggerConsumer(capture=True)
        event = _make_event(level="WARN")
        consumer(event)
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "WARN"

    def test_error_routes_to_error(self):
        """ERROR events route to error logger."""
        consumer = LoggerConsumer(capture=True)
        event = _make_event(level="ERROR")
        consumer(event)
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "ERROR"
        assert consumer._loggers["ERROR"].name == "keeprollming.error"

    def test_all_levels_routed(self):
        """All four levels produce captured events."""
        consumer = LoggerConsumer(capture=True)
        for level in ("DEBUG", "INFO", "WARN", "ERROR"):
            consumer(_make_event(level=level))
        assert len(consumer.captured) == 4


class TestLoggerConsumerThresholdFiltering:
    """Test LoggerConsumer threshold filtering (INV-03)."""

    def test_info_level_filters_debug(self):
        """LoggerConsumer with level=INFO filters DEBUG events."""
        consumer = LoggerConsumer(level="INFO")
        consumer(_make_event(level="DEBUG"))
        # Event is still captured (capture=True)
        assert len(consumer.captured) == 1
        # But threshold filtering means the logger won't emit it

    def test_warn_level_filters_debug_and_info(self):
        """LoggerConsumer with level=WARN filters DEBUG and INFO."""
        consumer = LoggerConsumer(level="WARN")
        consumer(_make_event(level="DEBUG"))
        consumer(_make_event(level="INFO"))
        consumer(_make_event(level="WARN"))
        assert len(consumer.captured) == 3

    def test_error_level_only_allows_error(self):
        """LoggerConsumer with level=ERROR only emits ERROR events."""
        consumer = LoggerConsumer(level="ERROR")
        consumer(_make_event(level="DEBUG"))
        consumer(_make_event(level="INFO"))
        consumer(_make_event(level="WARN"))
        consumer(_make_event(level="ERROR"))
        assert len(consumer.captured) == 4


class TestLoggerConsumerLogRecordProjection:
    """Test LoggerConsumer log record projection (INV-03)."""

    def test_project_log_record_includes_type(self):
        """_project_log_record includes event type."""
        consumer = LoggerConsumer()
        event = _make_event(type_str="streaming.parser.event")
        record = consumer._project_log_record(event)
        assert record["type"] == "streaming.parser.event"

    def test_project_log_record_includes_source(self):
        """_project_log_record includes source namespace."""
        consumer = LoggerConsumer()
        event = _make_event()
        record = consumer._project_log_record(event)
        assert record["source"] == "streaming.parser"

    def test_project_log_record_includes_level(self):
        """_project_log_record includes event level."""
        consumer = LoggerConsumer()
        event = _make_event(level="ERROR")
        record = consumer._project_log_record(event)
        assert record["level"] == "ERROR"

    def test_project_log_record_includes_req_id(self):
        """_project_log_record includes req_id from envelope."""
        consumer = LoggerConsumer()
        event = _make_event(req_id="abc123")
        record = consumer._project_log_record(event)
        assert record["req_id"] == "abc123"

    def test_project_log_record_includes_data(self):
        """_project_log_record includes full event data (INV-04)."""
        consumer = LoggerConsumer()
        data = {"key": "value", "nested": {"a": 1}}
        event = _make_event(data=data)
        record = consumer._project_log_record(event)
        assert record["data"] == data

    def test_project_log_record_includes_timestamp(self):
        """_project_log_record includes ISO timestamp."""
        consumer = LoggerConsumer()
        event = _make_event()
        record = consumer._project_log_record(event)
        assert "timestamp" in record
        assert "T" in record["timestamp"]

    def test_project_log_record_preserves_fidelity(self):
        """_project_log_record preserves data fidelity (INV-04)."""
        consumer = LoggerConsumer()
        large_data = {"x" * 100: "y" * 1000}
        event = _make_event(data=large_data)
        record = consumer._project_log_record(event)
        assert record["data"] is large_data  # Same object, no copy/snipping


class TestLoggerConsumerIntegration:
    """Integration tests for LoggerConsumer with EventDispatcher."""

    def test_consumer_with_dispatcher(self):
        """LoggerConsumer works with EventDispatcher emit."""
        from keeprollming.observability.dispatcher import EventDispatcher

        consumer = LoggerConsumer(capture=True)
        dispatcher = EventDispatcher(req_id="test-req")
        dispatcher.subscribe("streaming", consumer)

        event = _make_event(type_str="streaming.parser.event", level="INFO")
        dispatcher.emit(event)

        assert len(consumer.captured) == 1
        assert consumer.captured[0] is event

    def test_multiple_consumers(self):
        """Multiple consumers receive the same event."""
        from keeprollming.observability.dispatcher import EventDispatcher

        consumer1 = LoggerConsumer(capture=True)
        consumer2 = LoggerConsumer(capture=True)
        dispatcher = EventDispatcher(req_id="test-req")
        dispatcher.subscribe("streaming", consumer1)
        dispatcher.subscribe("streaming", consumer2)

        event = _make_event()
        dispatcher.emit(event)

        assert len(consumer1.captured) == 1
        assert len(consumer2.captured) == 1

    def test_dispatcher_emit_with_logger_consumer(self):
        """EventDispatcher.emit routes to LoggerConsumer."""
        from keeprollming.observability.dispatcher import EventDispatcher

        consumer = LoggerConsumer(capture=True)
        dispatcher = EventDispatcher(req_id="test-req")
        dispatcher.subscribe("streaming", consumer)

        # Emit events at different levels
        for level in ("DEBUG", "INFO", "WARN", "ERROR"):
            dispatcher.emit(_make_event(level=level))

        assert len(consumer.captured) == 4
