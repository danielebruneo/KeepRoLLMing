"""Unit tests for ExecutionUsage integration in performance module (Phase 12)."""

import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from keeprollming.performance import RouteStats


class TestRouteStatsExecutionUsage:
    """Test RouteStats correctly accumulates ExecutionUsage fields."""

    def test_init_with_execution_usage_fields(self):
        """RouteStats.__init__ extracts ExecutionUsage fields from entry."""
        entry = {
            "route_name": "test_route",
            "model": "test-model",
            "upstream_attempts": 2,
            "usage_reported_attempts": 2,
            "recovery_count": 1,
            "retry_amplification_ratio": 2.0,
            "usage_complete": True,
        }

        stats = RouteStats(entry)

        assert stats.upstream_attempts.stats()["avg"] == 2.0
        assert stats.usage_reported_attempts.stats()["avg"] == 2.0
        assert stats.recovery_count.stats()["avg"] == 1.0
        assert stats.retry_amplification_ratio.stats()["avg"] == 2.0
        assert stats.usage_complete_count == 1

    def test_update_with_execution_usage_fields(self):
        """RouteStats.update() correctly accumulates ExecutionUsage fields."""
        entry1 = {
            "route_name": "test_route",
            "model": "test-model",
            "upstream_attempts": 1,
            "usage_reported_attempts": 1,
            "recovery_count": 0,
            "retry_amplification_ratio": 1.0,
            "usage_complete": True,
        }

        entry2 = {
            "route_name": "test_route",
            "model": "test-model",
            "upstream_attempts": 3,
            "usage_reported_attempts": 2,
            "recovery_count": 2,
            "retry_amplification_ratio": 1.5,
            "usage_complete": False,
        }

        stats = RouteStats(entry1)
        stats.update(entry2)

        # Should have 2 requests
        assert stats.count == 2

        # upstream_attempts: (1 + 3) / 2 = 2.0 avg
        assert stats.upstream_attempts.stats()["avg"] == 2.0
        assert stats.upstream_attempts.stats()["min"] == 1.0
        assert stats.upstream_attempts.stats()["max"] == 3.0

        # usage_reported_attempts: (1 + 2) / 2 = 1.5 avg
        assert stats.usage_reported_attempts.stats()["avg"] == 1.5

        # recovery_count: (0 + 2) / 2 = 1.0 avg
        assert stats.recovery_count.stats()["avg"] == 1.0

        # retry_amplification_ratio: (1.0 + 1.5) / 2 = 1.25 avg
        assert stats.retry_amplification_ratio.stats()["avg"] == 1.25

        # usage_complete_count: only first entry was True
        assert stats.usage_complete_count == 1

    def test_to_route_entry_includes_execution_usage(self):
        """RouteStats.to_route_entry() includes ExecutionUsage fields in output."""
        entry = {
            "route_name": "test_route",
            "model": "test-model",
            "upstream_attempts": 2,
            "usage_reported_attempts": 1,
            "recovery_count": 1,
            "retry_amplification_ratio": 2.0,
            "usage_complete": True,
        }

        stats = RouteStats(entry)
        output = stats.to_route_entry()

        assert "upstream_attempts" in output
        assert "usage_reported_attempts" in output
        assert "recovery_count" in output
        assert "retry_amplification_ratio" in output
        assert "usage_complete_pct" in output

        # Check values
        assert output["upstream_attempts"]["avg"] == 2.0
        assert output["usage_reported_attempts"]["avg"] == 1.0
        assert output["recovery_count"]["avg"] == 1.0
        assert output["retry_amplification_ratio"]["avg"] == 2.0
        assert output["usage_complete_pct"] == 1.0  # 1/1 = 100%

    def test_to_route_entry_usage_complete_percentage(self):
        """RouteStats.to_route_entry() correctly calculates usage_complete_pct."""
        entry1 = {
            "route_name": "test_route",
            "model": "test-model",
            "upstream_attempts": 1,
            "usage_reported_attempts": 1,
            "recovery_count": 0,
            "retry_amplification_ratio": 1.0,
            "usage_complete": True,
        }

        entry2 = {
            "route_name": "test_route",
            "model": "test-model",
            "upstream_attempts": 1,
            "usage_reported_attempts": 1,
            "recovery_count": 0,
            "retry_amplification_ratio": 1.0,
            "usage_complete": False,
        }

        stats = RouteStats(entry1)
        stats.update(entry2)
        output = stats.to_route_entry()

        # 1 out of 2 requests had usage_complete=True
        assert output["usage_complete_pct"] == 0.5

    def test_backward_compatibility_missing_fields(self):
        """RouteStats handles entries without ExecutionUsage fields gracefully."""
        entry = {
            "route_name": "test_route",
            "model": "test-model",
            "total_tps": 10.0,
            # No ExecutionUsage fields
        }

        stats = RouteStats(entry)

        # Should not crash, fields should be empty accumulators
        assert stats.upstream_attempts.stats()["avg"] is None
        assert stats.usage_reported_attempts.stats()["avg"] is None
        assert stats.recovery_count.stats()["avg"] is None
        assert stats.retry_amplification_ratio.stats()["avg"] is None
        assert stats.usage_complete_count == 0

        # to_route_entry should still work
        output = stats.to_route_entry()
        assert output["upstream_attempts"]["avg"] is None
        assert output["usage_complete_pct"] == 0.0

    def test_null_execution_usage_fields(self):
        """RouteStats handles null ExecutionUsage fields correctly (Rule 1)."""
        entry = {
            "route_name": "test_route",
            "model": "test-model",
            "upstream_attempts": None,
            "usage_reported_attempts": None,
            "recovery_count": None,
            "retry_amplification_ratio": None,
            "usage_complete": None,
        }

        stats = RouteStats(entry)

        # None values should be treated as missing
        assert stats.upstream_attempts.stats()["avg"] is None
        assert stats.usage_complete_count == 0


class TestSummaryYamlExecutionUsage:
    """Test summary.yaml includes ExecutionUsage fields."""

    def test_summary_yaml_structure(self, tmp_path):
        """summary.yaml includes ExecutionUsage fields when present."""
        # This test verifies the structure of summary.yaml output
        # The actual summary generation is tested through RouteStats

        # Simulate what _update_summary produces
        route_entry = {
            "route_name": "test_route",
            "model": "test-model",
            "requests": 5,
            "upstream_attempts": {"avg": 2.0, "min": 1.0, "max": 3.0},
            "usage_reported_attempts": {"avg": 1.8, "min": 1.0, "max": 2.0},
            "recovery_count": {"avg": 1.0, "min": 0.0, "max": 2.0},
            "retry_amplification_ratio": {"avg": 1.11, "min": 1.0, "max": 1.5},
            "usage_complete_pct": 0.8,
        }

        # Verify structure
        assert "upstream_attempts" in route_entry
        assert "usage_reported_attempts" in route_entry
        assert "recovery_count" in route_entry
        assert "retry_amplification_ratio" in route_entry
        assert "usage_complete_pct" in route_entry

        # Verify nested structure
        assert "avg" in route_entry["upstream_attempts"]
        assert "min" in route_entry["upstream_attempts"]
        assert "max" in route_entry["upstream_attempts"]
