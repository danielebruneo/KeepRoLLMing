"""Unit tests for observability formatters (Phase O5)."""

import json
import time

import pytest

from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.formatters import (
    FORMAT_ERROR,
    CompactFormatter,
    Formatter,
    JsonFormatter,
    PlainTextFormatter,
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


class TestJsonFormatter:
    """Test JsonFormatter behavior."""

    def test_json_formatter_basic(self):
        """JsonFormatter produces valid JSON with required fields."""
        formatter = JsonFormatter()
        event = _make_event()
        result = formatter.format(event)

        parsed = json.loads(result)
        assert parsed["type"] == "streaming.parser.event"
        assert parsed["source"]["domain"] == "streaming"
        assert parsed["source"]["component"] == "parser"
        assert parsed["level"] == "INFO"
        assert parsed["req_id"] == "test-req"
        assert "timestamp_ms" in parsed

    def test_json_formatter_with_all_fields(self):
        """JsonFormatter includes all envelope fields in output."""
        formatter = JsonFormatter()
        event = RuntimeEvent(
            type="filter.chain.executed",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="filter", component="chain", instance="nudge"),
            data={"nudge": "retry", "attempt": 2},
            req_id="req-123",
            level="WARN",
            trace_id="trace-abc",
            span_id="span-def",
        )
        result = formatter.format(event)
        parsed = json.loads(result)

        assert parsed["type"] == "filter.chain.executed"
        assert parsed["source"]["instance"] == "nudge"
        assert parsed["level"] == "WARN"
        assert parsed["req_id"] == "req-123"
        assert parsed["trace_id"] == "trace-abc"
        assert parsed["span_id"] == "span-def"
        assert parsed["data"] == {"nudge": "retry", "attempt": 2}

    def test_json_formatter_timestamp_is_float(self):
        """JsonFormatter converts timestamp_ns to millisecond float."""
        formatter = JsonFormatter()
        event = _make_event()
        result = formatter.format(event)
        parsed = json.loads(result)

        assert isinstance(parsed["timestamp_ms"], float)
        assert parsed["timestamp_ms"] > 0

    def test_json_formatter_empty_data(self):
        """JsonFormatter handles empty data dict."""
        formatter = JsonFormatter()
        event = RuntimeEvent(
            type="system.starting",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="system", component="startup"),
            data={},
        )
        result = formatter.format(event)
        parsed = json.loads(result)
        assert parsed["data"] == {}


class TestPlainTextFormatter:
    """Test PlainTextFormatter behavior (D-072 §8)."""

    def test_plain_text_formatter(self):
        """PlainTextFormatter produces human-readable single-line output."""
        formatter = PlainTextFormatter()
        event = _make_event()
        result = formatter.format(event)

        # New format: timestamp [req_id] event_type key=value ...
        assert "streaming.event" in result
        assert "[test-req]" in result
        # Should contain ISO timestamp with milliseconds
        assert "T" in result and "Z" in result

    def test_plain_text_formatter_with_req_id(self):
        """PlainTextFormatter includes req_id as bracketed tag."""
        formatter = PlainTextFormatter()
        event = _make_event(req_id="req-abc")
        result = formatter.format(event)
        assert "[req-abc]" in result

    def test_plain_text_formatter_without_req_id(self):
        """PlainTextFormatter omits req_id tag when None."""
        formatter = PlainTextFormatter()
        event = _make_event(req_id=None)
        result = formatter.format(event)
        # No bracketed req_id tag should appear
        assert "[None]" not in result

    def test_plain_text_formatter_with_data(self):
        """PlainTextFormatter renders data as key=value pairs, not JSON dumps."""
        formatter = PlainTextFormatter()
        event = _make_event(data={"key": "value", "count": 42})
        result = formatter.format(event)
        # Data rendered as key=value, not JSON object
        assert 'key="value"' in result or "key=value" in result
        assert "count=42" in result

    def test_plain_text_formatter_not_json_dump(self):
        """PlainTextFormatter output is NOT a RuntimeEvent JSON dump (D-072 §8)."""
        formatter = PlainTextFormatter()
        event = _make_event(data={"test": True})
        result = formatter.format(event)
        # Should not look like a JSON object
        assert not result.strip().startswith("{")
        assert '"type":' not in result


class TestCompactFormatter:
    """Test CompactFormatter behavior."""

    def test_compact_formatter(self):
        """CompactFormatter produces minimal single-line output."""
        formatter = CompactFormatter()
        event = _make_event()
        result = formatter.format(event)

        # Should be a single line
        assert "\n" not in result
        assert result.startswith("ts_ms=")
        assert "method=POST" in result
        assert "path=/v1/chat/completions" in result

    def test_compact_formatter_with_req_id(self):
        """CompactFormatter includes req_id when present."""
        formatter = CompactFormatter()
        event = _make_event(req_id="req-xyz")
        result = formatter.format(event)
        assert "req-xyz" in result

    def test_compact_formatter_without_req_id(self):
        """CompactFormatter omits req_id when None."""
        formatter = CompactFormatter()
        event = _make_event(req_id=None)
        result = formatter.format(event)
        assert "req_id" not in result


class TestFormatterNeverRaises:
    """Test that formatters never raise exceptions."""

    def test_formatter_never_raises(self):
        """All formatters return FORMAT_ERROR on exception, never raise."""
        event = _make_event()

        for formatter_cls in [JsonFormatter, PlainTextFormatter, CompactFormatter]:
            formatter = formatter_cls()
            result = formatter.format(event)
            assert isinstance(result, str)
            assert len(result) > 0

    def test_formatter_stateless(self):
        """Same input → same output (stateless formatters)."""
        event = _make_event()

        for formatter_cls in [JsonFormatter, PlainTextFormatter, CompactFormatter]:
            formatter = formatter_cls()
            result1 = formatter.format(event)
            result2 = formatter.format(event)
            assert result1 == result2


class TestFormatErrorConstant:
    """Test FORMAT_ERROR sentinel."""

    def test_format_error_is_string(self):
        """FORMAT_ERROR is a non-empty string."""
        assert isinstance(FORMAT_ERROR, str)
        assert len(FORMAT_ERROR) > 0
        assert "[FORMAT_ERROR]" in FORMAT_ERROR
