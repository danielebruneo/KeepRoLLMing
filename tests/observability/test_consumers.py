"""Unit tests for consumer stubs (Phase O2)."""

import time

import pytest

from keeprollming.observability.consumers import (
    LoggerConsumer,
    MetricsConsumer,
    PerformanceConsumer,
)
from keeprollming.observability.events import EventSource, RuntimeEvent


def _make_event(type_str="streaming.parser.event", level="DEBUG"):
    """Helper to create a minimal RuntimeEvent."""
    return RuntimeEvent(
        type=type_str,
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="streaming", component="parser"),
        data={"test": True},
        level=level,
    )


class TestLoggerConsumer:
    """Test LoggerConsumer routing."""

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

    def test_clear(self):
        """LoggerConsumer.clear() resets captured events."""
        consumer = LoggerConsumer(capture=True)
        consumer(_make_event())
        consumer(_make_event())
        assert len(consumer.captured) == 2
        consumer.clear()
        assert len(consumer.captured) == 0

    def test_level_routing_debug(self):
        """LoggerConsumer routes DEBUG events."""
        consumer = LoggerConsumer(capture=True)
        consumer(_make_event(level="DEBUG"))
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "DEBUG"

    def test_level_routing_info(self):
        """LoggerConsumer routes INFO events."""
        consumer = LoggerConsumer(capture=True)
        consumer(_make_event(level="INFO"))
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "INFO"

    def test_level_routing_warn(self):
        """LoggerConsumer routes WARN events."""
        consumer = LoggerConsumer(capture=True)
        consumer(_make_event(level="WARN"))
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "WARN"

    def test_level_routing_error(self):
        """LoggerConsumer routes ERROR events."""
        consumer = LoggerConsumer(capture=True)
        consumer(_make_event(level="ERROR"))
        assert len(consumer.captured) == 1
        assert consumer.captured[0].level == "ERROR"


class TestMetricsConsumer:
    """Test MetricsConsumer."""

    def test_call_captures_event(self):
        """MetricsConsumer captures events when capture=True."""
        consumer = MetricsConsumer(capture=True)
        event = _make_event(type_str="execution.usage")
        consumer(event)
        assert len(consumer.captured) == 1

    def test_call_does_not_capture_when_capture_false(self):
        """MetricsConsumer does not capture when capture=False."""
        consumer = MetricsConsumer(capture=False)
        consumer(_make_event())
        assert len(consumer.captured) == 0

    def test_clear(self):
        """MetricsConsumer.clear() resets captured events."""
        consumer = MetricsConsumer(capture=True)
        consumer(_make_event())
        consumer.clear()
        assert len(consumer.captured) == 0


class TestPerformanceConsumer:
    """Test PerformanceConsumer."""

    def test_call_captures_event(self):
        """PerformanceConsumer captures events when capture=True."""
        consumer = PerformanceConsumer(capture=True)
        event = _make_event(type_str="execution.usage")
        consumer(event)
        assert len(consumer.captured) == 1

    def test_call_does_not_capture_when_capture_false(self):
        """PerformanceConsumer does not capture when capture=False."""
        consumer = PerformanceConsumer(capture=False)
        consumer(_make_event())
        assert len(consumer.captured) == 0

    def test_clear(self):
        """PerformanceConsumer.clear() resets captured events."""
        consumer = PerformanceConsumer(capture=True)
        consumer(_make_event())
        consumer.clear()
        assert len(consumer.captured) == 0
