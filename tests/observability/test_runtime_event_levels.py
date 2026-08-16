"""Unit tests for extended RuntimeEvent levels (D-072, Phase P1).

Tests TRACE and BASIC level support, level hierarchy, and validation.
"""

import time

import pytest

from keeprollming.observability.events import (
    EventSource,
    LEVEL_ORDER,
    RuntimeEvent,
    level_at_or_above,
)


def _make_event(level: str = "INFO") -> RuntimeEvent:
    """Helper to create a minimal RuntimeEvent with specified level."""
    return RuntimeEvent(
        type="test.event",
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="test", component="unit"),
        data={},
        level=level,
    )


class TestRuntimeEventTraceBasicLevels:
    """Test TRACE and BASIC level acceptance."""

    def test_trace_level_accepted(self):
        """TRACE is accepted as a valid level."""
        event = _make_event(level="TRACE")
        assert event.level == "TRACE"

    def test_basic_level_accepted(self):
        """BASIC is accepted as a valid level."""
        event = _make_event(level="BASIC")
        assert event.level == "BASIC"

    def test_all_valid_levels_accepted(self):
        """All levels in LEVEL_ORDER are accepted."""
        for level in LEVEL_ORDER:
            event = _make_event(level=level)
            assert event.level == level

    def test_invalid_level_rejected(self):
        """Invalid levels raise ValueError in __post_init__."""
        invalid_levels = ["CRITICAL", "VERBOSE", "QUIET", "", "info", "Info"]
        for lvl in invalid_levels:
            with pytest.raises(ValueError, match="must be TRACE/DEBUG/INFO/BASIC/WARN/ERROR"):
                RuntimeEvent(
                    type="test.event",
                    timestamp_ns=time.time_ns(),
                    source=EventSource(domain="test", component="unit"),
                    data={},
                    level=lvl,
                )


class TestLevelHierarchyComparison:
    """Test level hierarchy ordering and comparison helper."""

    def test_level_order_is_correct(self):
        """LEVEL_ORDER defines the correct hierarchy."""
        assert LEVEL_ORDER == ("TRACE", "DEBUG", "INFO", "BASIC", "WARN", "ERROR")

    def test_trace_is_lowest(self):
        """TRACE is the lowest level (index 0)."""
        assert LEVEL_ORDER.index("TRACE") == 0

    def test_error_is_highest(self):
        """ERROR is the highest level."""
        assert LEVEL_ORDER.index("ERROR") == len(LEVEL_ORDER) - 1

    def test_basic_between_info_and_warn(self):
        """BASIC sits between INFO and WARN in the hierarchy."""
        info_idx = LEVEL_ORDER.index("INFO")
        basic_idx = LEVEL_ORDER.index("BASIC")
        warn_idx = LEVEL_ORDER.index("WARN")
        assert info_idx < basic_idx < warn_idx

    def test_level_at_or_above_same_level(self):
        """Same level is considered at or above."""
        for level in LEVEL_ORDER:
            assert level_at_or_above(level, level) is True, f"{level} should be >= {level}"

    def test_level_at_or_above_higher_passes(self):
        """Higher levels pass the check."""
        assert level_at_or_above("ERROR", "WARN") is True
        assert level_at_or_above("WARN", "INFO") is True
        assert level_at_or_above("BASIC", "DEBUG") is True
        assert level_at_or_above("INFO", "TRACE") is True

    def test_level_at_or_above_lower_fails(self):
        """Lower levels fail the check."""
        assert level_at_or_above("TRACE", "DEBUG") is False
        assert level_at_or_above("DEBUG", "INFO") is False
        assert level_at_or_above("INFO", "BASIC") is False
        assert level_at_or_above("WARN", "ERROR") is False

    def test_level_at_or_above_trace_minimum(self):
        """All levels are at or above TRACE."""
        for level in LEVEL_ORDER:
            assert level_at_or_above(level, "TRACE") is True, f"{level} should be >= TRACE"

    def test_level_at_or_above_error_minimum(self):
        """Only ERROR is at or above ERROR."""
        for level in LEVEL_ORDER:
            expected = level == "ERROR"
            assert level_at_or_above(level, "ERROR") is expected, f"{level} vs ERROR"

    def test_level_at_or_above_basic_minimum(self):
        """BASIC, WARN, and ERROR are at or above BASIC."""
        for level in LEVEL_ORDER:
            expected = level in ("BASIC", "WARN", "ERROR")
            assert level_at_or_above(level, "BASIC") is expected, f"{level} vs BASIC"

    def test_level_at_or_above_invalid_raises(self):
        """Invalid levels raise ValueError."""
        with pytest.raises(ValueError, match="Invalid level"):
            level_at_or_above("INVALID", "INFO")

        with pytest.raises(ValueError, match="Invalid level"):
            level_at_or_above("INFO", "INVALID")
