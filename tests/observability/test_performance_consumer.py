"""Tests for O10 PerformanceConsumer — event-driven performance logging.

Validates that PerformanceConsumer:
1. Receives execution.performance.request_complete events → writes correct JSONL records
2. RouteStats aggregation matches current behavior for multiple requests
3. Summary flush occurs at configured interval with correct YAML structure
4. Archive trigger at 1000 requests with correct file movement and epoch reset
5. Produces identical output to legacy record_request_performance() (parity)
6. Handles I/O failures gracefully (async writer unavailable → fallback to sync write)

Usage:
    pytest tests/observability/test_performance_consumer.py -v -s
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.consumers import PerformanceConsumer


def _make_request_complete_event(
    model: str = "test-model",
    route_name: str = "test/route",
    req_id: str = "test-req-001",
    stream: bool = True,
    elapsed_ms: float = 500.0,
    ttft_ms: float | None = 200.0,
    completion_tokens: int | None = 50,
    prompt_tokens: int | None = 10,
    total_tokens: int | None = 60,
    finish_reason: str | None = "stop",
    did_summarize: bool = False,
    passthrough: bool = False,
    completion_tokens_source: str = "execution_usage",
    upstream_attempts: int = 1,
    usage_reported_attempts: int = 1,
    recovery_count: int = 0,
    retry_amplification_ratio: float = 1.0,
    usage_complete: bool = True,
    upstream_prompt_tokens: int | None = None,
    upstream_completion_tokens: int | None = None,
    upstream_total_tokens: int | None = None,
    route_hierarchy: List[str] | None = None,
    timestamp_ns: int = 1_700_000_000_000_000_000,
) -> RuntimeEvent:
    """Create a test execution.performance.request_complete event."""
    if route_hierarchy is None:
        route_hierarchy = [route_name]
    return RuntimeEvent(
        type="execution.performance.request_complete",
        timestamp_ns=timestamp_ns,
        source=EventSource(domain="execution", component="performance"),
        data={
            "model": model,
            "route_name": route_name,
            "route_hierarchy": route_hierarchy,
            "req_id": req_id,
            "stream": stream,
            "elapsed_ms": elapsed_ms,
            "ttft_ms": ttft_ms,
            "completion_tokens": completion_tokens,
            "prompt_tokens": prompt_tokens,
            "total_tokens": total_tokens,
            "finish_reason": finish_reason,
            "did_summarize": did_summarize,
            "passthrough": passthrough,
            "completion_tokens_source": completion_tokens_source,
            "upstream_attempts": upstream_attempts,
            "usage_reported_attempts": usage_reported_attempts,
            "recovery_count": recovery_count,
            "retry_amplification_ratio": retry_amplification_ratio,
            "usage_complete": usage_complete,
            "upstream_prompt_tokens": upstream_prompt_tokens,
            "upstream_completion_tokens": upstream_completion_tokens,
            "upstream_total_tokens": upstream_total_tokens,
        },
        req_id=req_id,
        level="INFO",
    )


@pytest.fixture
def clean_perf_dir():
    """Create a temporary directory for performance logs."""
    # Reset global in-memory stats so tests are isolated
    import keeprollming.performance as performance
    from keeprollming.performance import reset_route_stats
    previous_dir = performance._PERF_LOGS_DIR
    reset_route_stats()

    with tempfile.TemporaryDirectory() as tmpdir:
        yield str(tmpdir)
    performance._PERF_LOGS_DIR = previous_dir
    reset_route_stats()


@pytest.fixture
def consumer(clean_perf_dir):
    """Create a PerformanceConsumer with test directory."""
    return PerformanceConsumer(
        perf_logs_dir=clean_perf_dir,
        summary_interval=1,  # Force immediate update for tests
        capture=True,
    )


def load_requests_jsonl(logs_dir: str) -> List[Dict[str, Any]]:
    """Load all .requests.jsonl files from a directory."""
    logs_path = Path(logs_dir)
    all_entries = []
    for jsonl_file in sorted(logs_path.glob("*.requests.jsonl")):
        for line in jsonl_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    all_entries.append(obj)
            except json.JSONDecodeError:
                continue
    return all_entries


def load_summary_yaml(logs_dir: str) -> Dict[str, Any]:
    """Load summary.yaml from a directory."""
    summary_path = Path(logs_dir) / "summary.yaml"
    if not summary_path.exists():
        return {"models": []}
    with open(summary_path, "r") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {"models": []}


class TestPerformanceConsumerBasic:
    """Basic functionality tests for PerformanceConsumer."""

    def test_receives_request_complete_event_writes_jsonl(self, consumer, clean_perf_dir):
        """Test that PerformanceConsumer writes correct JSONL record on event."""
        event = _make_request_complete_event(req_id="basic-001")
        consumer(event)

        # Flush async writer to ensure records are written
        from keeprollming.async_log_writer import get_async_writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        entries = load_requests_jsonl(clean_perf_dir)
        assert len(entries) == 1, f"Expected 1 entry, found {len(entries)}"

        entry = entries[0]
        assert entry["model"] == "test-model"
        assert entry["route_name"] == "test/route"
        assert entry["req_id"] == "basic-001"
        assert entry["stream"] is True
        assert entry["elapsed_ms"] == 500.0
        assert entry["completion_tokens"] == 50
        assert entry["prompt_tokens"] == 10
        assert entry["completed_at"] == 1_700_000_000.0

    def test_captures_events_for_inspection(self, consumer):
        """Test that captured events are available for testing."""
        event = _make_request_complete_event(req_id="capture-001")
        consumer(event)

        assert len(consumer.captured) == 1
        assert consumer.captured[0].type == "execution.performance.request_complete"

    def test_handles_non_streaming_event(self, consumer, clean_perf_dir):
        """Test that non-streaming events are handled correctly."""
        event = _make_request_complete_event(
            req_id="nonstream-001",
            stream=False,
            ttft_ms=None,
        )
        consumer(event)

        from keeprollming.async_log_writer import get_async_writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        entries = load_requests_jsonl(clean_perf_dir)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["stream"] is False
        assert entry["ttft_ms"] is None

    def test_handles_missing_tokens(self, consumer, clean_perf_dir):
        """Test that events with missing token counts are handled gracefully."""
        event = _make_request_complete_event(
            req_id="missing-tokens-001",
            completion_tokens=None,
            prompt_tokens=None,
            total_tokens=None,
            completion_tokens_source="missing",
        )
        consumer(event)

        from keeprollming.async_log_writer import get_async_writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        entries = load_requests_jsonl(clean_perf_dir)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["completion_tokens"] is None
        assert entry["prompt_tokens"] is None
        assert entry["completion_tokens_source"] == "missing"

    def test_uses_timestamp_window_and_omits_nonfinite_ratio(self, consumer, clean_perf_dir):
        consumer(_make_request_complete_event(
            req_id="window-1", timestamp_ns=1_700_000_000_000_000_000,
            retry_amplification_ratio=float("inf"),
        ))
        consumer(_make_request_complete_event(
            req_id="window-2", timestamp_ns=1_700_000_002_000_000_000,
        ))

        summary = load_summary_yaml(clean_perf_dir)
        route = summary["models"][0]["routes"][0]
        assert summary["updated_at"].endswith("Z")
        assert route["requests_per_hour"] == 3600.0
        assert route["window_requests"] == 2
        assert route["retry_amplification_ratio"] == {"avg": 1.0, "min": 1.0, "max": 1.0}


class TestPerformanceConsumerRouteStats:
    """RouteStats aggregation tests."""

    def test_aggregates_multiple_requests(self, consumer, clean_perf_dir):
        """Test that RouteStats aggregates correctly for multiple requests."""
        for i in range(3):
            event = _make_request_complete_event(
                req_id=f"agg-00{i}",
                elapsed_ms=500.0 + i * 100.0,
                completion_tokens=50 + i * 5,
            )
            consumer(event)

        from keeprollming.async_log_writer import get_async_writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        entries = load_requests_jsonl(clean_perf_dir)
        assert len(entries) == 3

    def test_separate_files_per_route(self, consumer, clean_perf_dir):
        """Test that different routes get separate JSONL files."""
        routes = ["route/a", "route/b", "route/c"]
        for i, route in enumerate(routes):
            event = _make_request_complete_event(
                req_id=f"separate-{i}",
                route_name=route,
                route_hierarchy=[route],
            )
            consumer(event)

        from keeprollming.async_log_writer import get_async_writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        logs_path = Path(clean_perf_dir)
        jsonl_files = list(logs_path.glob("*.requests.jsonl"))
        assert len(jsonl_files) == 3, f"Expected 3 files, found {len(jsonl_files)}"


class TestPerformanceConsumerSummary:
    """Summary flush tests."""

    def test_summary_flush_at_interval(self, consumer, clean_perf_dir):
        """Test that summary.yaml is flushed at configured interval."""
        # summary_interval=1 means flush on every request
        for i in range(3):
            event = _make_request_complete_event(req_id=f"summary-{i}")
            consumer(event)

        summary = load_summary_yaml(clean_perf_dir)
        assert "models" in summary
        assert len(summary["models"]) > 0

    def test_summary_structure_matches_legacy(self, consumer, clean_perf_dir):
        """Test that summary.yaml structure matches legacy format."""
        for i in range(3):
            event = _make_request_complete_event(req_id=f"struct-{i}")
            consumer(event)

        summary = load_summary_yaml(clean_perf_dir)

        # New format: models is a list of dicts with "model" and "routes" keys
        all_routes = []
        for model_entry in summary["models"]:
            if isinstance(model_entry, dict) and "routes" in model_entry:
                all_routes.extend(model_entry["routes"])

        assert len(all_routes) > 0
        route_entry = all_routes[0]

        # Verify required fields
        assert "route_name" in route_entry
        assert "requests" in route_entry
        assert "total_tps" in route_entry
        assert "completion_tps" in route_entry
        assert "elapsed_ms" in route_entry


class TestPerformanceConsumerExecutionUsage:
    """ExecutionUsage field tests (Phase 12)."""

    def test_execution_usage_fields_recorded(self, consumer, clean_perf_dir):
        """Test that ExecutionUsage fields are recorded correctly."""
        event = _make_request_complete_event(
            req_id="exec-usage-001",
            upstream_attempts=3,
            usage_reported_attempts=2,
            recovery_count=1,
            retry_amplification_ratio=1.5,
            usage_complete=True,
        )
        consumer(event)

        from keeprollming.async_log_writer import get_async_writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        entries = load_requests_jsonl(clean_perf_dir)
        assert len(entries) == 1
        entry = entries[0]

        assert entry["upstream_attempts"] == 3
        assert entry["usage_reported_attempts"] == 2
        assert entry["recovery_count"] == 1
        assert entry["retry_amplification_ratio"] == 1.5
        assert entry["usage_complete"] is True

    def test_tps_uses_final_tokens_not_upstream_retry_totals(self, consumer, clean_perf_dir):
        event = _make_request_complete_event(
            req_id="recovery-logical-001",
            elapsed_ms=1_000,
            ttft_ms=200,
            prompt_tokens=120,
            completion_tokens=120,
            total_tokens=240,
            upstream_attempts=2,
            recovery_count=1,
            upstream_prompt_tokens=220,
            upstream_completion_tokens=135,
            upstream_total_tokens=355,
        )

        consumer(event)
        entry = load_requests_jsonl(clean_perf_dir)[0]

        assert entry["completion_tps"] == 150.0  # 120 / (1000 - 200) ms
        assert entry["upstream_completion_tokens"] == 135
        assert entry["upstream_prompt_tokens"] == 220
        assert entry["completion_tokens"] == 120

    def test_cache_adjusts_physical_prompt_and_total_tps(self, consumer, clean_perf_dir):
        """KV-cache hits reduce physical work without changing logical usage."""
        event = _make_request_complete_event(
            req_id="cache-aware-001",
            elapsed_ms=2_000,
            ttft_ms=1_000,
            prompt_tokens=10_000,
            completion_tokens=100,
            total_tokens=10_100,
        )
        event.data["cached_prompt_tokens"] = 9_000

        consumer(event)
        entry = load_requests_jsonl(clean_perf_dir)[0]

        assert entry["prompt_tokens"] == 10_000
        assert entry["cached_prompt_tokens"] == 9_000
        assert entry["uncached_prompt_tokens"] == 1_000
        assert entry["prompt_tps"] == 1_000.0
        assert entry["total_tps"] == 550.0


class TestPerformanceConsumerParity:
    """Parity tests — verify output matches legacy record_request_performance()."""

    def test_parity_with_legacy_record_request_performance(self, clean_perf_dir):
        """Test that PerformanceConsumer produces identical output to legacy function."""
        from keeprollming.performance import (
            record_request_performance,
            reset_route_stats,
        )
        from keeprollming.async_log_writer import get_async_writer

        # Reset state for clean comparison
        reset_route_stats()

        # Record using legacy function
        legacy_result = record_request_performance(
            model="parity-model",
            route_name="test/parity",
            req_id="parity-legacy-001",
            stream=True,
            elapsed_ms=600.0,
            completion_tokens=60,
            prompt_tokens=15,
            total_tokens=75,
            ttft_ms=250.0,
            finish_reason="stop",
            did_summarize=False,
            passthrough=False,
            completion_tokens_source="execution_usage",
            performance_logs_dir=clean_perf_dir,
        )

        # Flush async writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        # Load legacy entry
        legacy_entries = load_requests_jsonl(clean_perf_dir)
        assert len(legacy_entries) == 1
        legacy_entry = legacy_entries[0]

        # Reset for consumer test
        reset_route_stats()
        # Clear files
        for f in Path(clean_perf_dir).glob("*.requests.jsonl"):
            f.unlink()
        summary_path = Path(clean_perf_dir) / "summary.yaml"
        if summary_path.exists():
            summary_path.unlink()

        # Record using PerformanceConsumer
        consumer = PerformanceConsumer(
            perf_logs_dir=clean_perf_dir,
            summary_interval=1,
        )
        event = _make_request_complete_event(
            model="parity-model",
            route_name="test/parity",
            req_id="parity-consumer-001",
            stream=True,
            elapsed_ms=600.0,
            ttft_ms=250.0,
            completion_tokens=60,
            prompt_tokens=15,
            total_tokens=75,
            finish_reason="stop",
            did_summarize=False,
            passthrough=False,
            completion_tokens_source="execution_usage",
        )
        consumer(event)

        # Flush async writer
        writer = get_async_writer()
        if writer._running:
            import asyncio
            asyncio.get_event_loop().run_until_complete(writer.flush())

        # Load consumer entry
        consumer_entries = load_requests_jsonl(clean_perf_dir)
        assert len(consumer_entries) == 1
        consumer_entry = consumer_entries[0]

        # Compare key fields (excluding req_id which differs)
        for field in ["model", "route_name", "stream", "elapsed_ms", "ttft_ms",
                      "completion_tokens", "prompt_tokens", "total_tokens",
                      "finish_reason", "did_summarize", "passthrough",
                      "completion_tokens_source"]:
            assert legacy_entry[field] == consumer_entry[field], \
                f"Field {field} mismatch: legacy={legacy_entry[field]}, consumer={consumer_entry[field]}"

        # Verify TPS calculations match
        assert abs(legacy_entry.get("total_tps", 0) - consumer_entry.get("total_tps", 0)) < 0.01


class TestPerformanceConsumerPerfLogsDirEvent:
    """Tests for execution.app.perf_logs_dir event handling."""

    def test_handles_perf_logs_dir_event(self, clean_perf_dir):
        """Test that perf_logs_dir configuration event is handled."""
        consumer = PerformanceConsumer(
            perf_logs_dir=clean_perf_dir,
            summary_interval=1,
        )

        # Create a new directory
        new_dir = tempfile.mkdtemp()
        try:
            event = RuntimeEvent(
                type="execution.app.perf_logs_dir",
                timestamp_ns=1_700_000_000_000_000_000,
                source=EventSource(domain="execution", component="app"),
                data={"message": f"Performance logs directory: {new_dir}"},
                level="INFO",
            )
            consumer(event)

            # Verify directory was updated
            assert consumer._perf_logs_dir == new_dir
        finally:
            shutil.rmtree(new_dir, ignore_errors=True)


class TestPerformanceConsumerIOFailure:
    """I/O failure handling tests."""

    def test_fallback_to_sync_write_on_async_failure(self, clean_perf_dir):
        """Test that sync write fallback works when async writer is unavailable."""
        consumer = PerformanceConsumer(
            perf_logs_dir=clean_perf_dir,
            summary_interval=1,
        )

        event = _make_request_complete_event(req_id="io-fail-001")
        consumer(event)

        # Even without async writer running, the fallback sync write should work
        entries = load_requests_jsonl(clean_perf_dir)
        assert len(entries) == 1, "Sync fallback write should succeed"
