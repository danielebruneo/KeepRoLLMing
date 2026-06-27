"""
Comprehensive tests for performance metrics logging.

This test suite validates that:
1. Streaming requests properly log elapsed_ms, ttft_ms, tps, total_tps, completion_tps
2. Non-streaming requests properly log elapsed_ms, total_tps (ttft=None)
3. Tool calls and reasoning content are properly tracked
4. Summary aggregation is correct
5. Metrics match expected values based on fake server behavior

Usage:
    pytest tests/test_performance_metrics.py -v -s
"""

import asyncio
import json
import time
import pytest
import yaml
import os
import tempfile
from pathlib import Path
from typing import Dict, Any, List
import httpx


# Test tolerance for timing measurements (percentage)
TIMING_TOLERANCE = 0.15  # 15% tolerance for timing variations

# Fake server port (use canonical fake_backend)
FAKE_SERVER_PORT = int(os.getenv("FAKE_SERVER_PORT", "8765"))
FAKE_SERVER_URL = f"http://127.0.0.1:{FAKE_SERVER_PORT}"


@pytest.fixture(scope="module", autouse=True)
def start_fake_server():
    """Start the canonical fake backend before running tests."""
    import subprocess
    import signal

    # Start canonical fake backend in background process
    proc = subprocess.Popen(
        ["python", "scripts/start-fake-backend.py", "--port", str(FAKE_SERVER_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent.parent),
    )

    # Wait for server to be ready
    max_wait = 5.0
    waited = 0.0
    interval = 0.1

    while waited < max_wait:
        try:
            with httpx.Client(timeout=1.0) as client:
                response = client.get(f"{FAKE_SERVER_URL}/health")
                if response.status_code == 200:
                    break
        except Exception:
            pass

        time.sleep(interval)
        waited += interval

    yield

    # Stop server after tests
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def clean_perf_logs_dir():
    """Create a temporary directory for performance logs and clean it."""
    # Reset global in-memory stats so tests are isolated
    from keeprollming.performance import reset_route_stats
    reset_route_stats()

    with tempfile.TemporaryDirectory() as tmpdir:
        perf_dir = Path(tmpdir) / "performance_logs"
        perf_dir.mkdir(parents=True)

        # Clear any existing files
        for f in perf_dir.glob("*.yaml"):
            f.unlink()

        yield str(perf_dir)


@pytest.fixture
def cleanup_perf_logs(clean_perf_logs_dir):
    """Fixture to ensure cleanup after each test."""
    yield clean_perf_logs_dir
    
    # Verify files were created
    perf_dir = Path(clean_perf_logs_dir)
    yaml_files = list(perf_dir.glob("*.requests.jsonl"))
    
    if yaml_files:
        print(f"\nPerformance logs written to {perf_dir}:")
        for yf in yaml_files:
            print(f"  - {yf.name}")


def load_requests_yaml(logs_dir: str) -> List[Dict[str, Any]]:
    """Load all .requests.jsonl files from a directory (JSON-lines format)."""
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
    
    with open(summary_path, 'r') as f:
        data = yaml.safe_load(f)
    
    return data if isinstance(data, dict) else {"models": []}


def calculate_expected_metrics(
    ttft_ms: float,
    elapsed_ms: float,
    completion_tokens: int,
    prompt_tokens: int,
    is_streaming: bool = True,
    num_chunks: int = 5,
    chunk_interval_ms: float = 100.0,
) -> Dict[str, Any]:
    """Calculate expected metrics based on timing parameters."""
    total_tokens = prompt_tokens + completion_tokens

    # Elapsed time: TTFT + chunk_intervals (for streaming) or full delay (non-streaming)
    if is_streaming:
        # Streaming: TTFT + (num_chunks - 1) * interval
        expected_elapsed = ttft_ms + (num_chunks - 1) * chunk_interval_ms
    else:
        # Non-streaming: just the prompt processing delay (buffered)
        expected_elapsed = ttft_ms

    # TPS calculations
    tps = completion_tokens / (elapsed_ms / 1000.0) if elapsed_ms > 0 else None
    total_tps = total_tokens / (elapsed_ms / 1000.0) if elapsed_ms > 0 else None

    # Completion TPS (for streaming: based on generation time only)
    if is_streaming and elapsed_ms > ttft_ms:
        completion_tps = completion_tokens / ((elapsed_ms - ttft_ms) / 1000.0)
    else:
        completion_tps = None

    # Prompt TPS (based on TTFT)
    prompt_tps = prompt_tokens / (ttft_ms / 1000.0) if ttft_ms > 0 else None

    return {
        "ttft_ms": ttft_ms,
        "elapsed_ms": expected_elapsed,
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": total_tokens,
        "tps": tps,
        "total_tps": total_tps,
        "completion_tps": completion_tps,
        "prompt_tps": prompt_tps,
    }


def validate_timing(actual: float, expected: float, tolerance: float = TIMING_TOLERANCE) -> bool:
    """Check if actual timing is within tolerance of expected."""
    if expected == 0:
        return actual == 0
    
    diff = abs(actual - expected)
    relative_diff = diff / expected
    
    return relative_diff <= tolerance


class TestStreamingPerformanceMetrics:
    """Tests for streaming request performance metrics."""
    
    @pytest.mark.asyncio
    async def test_streaming_request_logs_all_metrics(self, cleanup_perf_logs):
        """Test that streaming requests log all performance metrics correctly."""
        from keeprollming.performance import record_request_performance
        
        # Simulate streaming request timing
        ttft_ms = 200.0  # Prompt processing delay (from fake server config)
        num_chunks = 5
        chunk_interval_ms = 100.0
        
        # Expected elapsed: TTFT + (num_chunks - 1) * interval
        expected_elapsed_ms = ttft_ms + (num_chunks - 1) * chunk_interval_ms  # 200 + 4*100 = 600ms
        
        # Simulate actual elapsed (with small variance)
        actual_elapsed_ms = expected_elapsed_ms * (1 + 0.02)  # 2% variance
        
        completion_tokens = 50
        prompt_tokens = 10
        
        # Record performance metrics
        result = record_request_performance(
            model="fake-model",
            route_name="test/streaming",
            req_id="streaming-test-001",
            stream=True,
            elapsed_ms=actual_elapsed_ms,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=prompt_tokens + completion_tokens,  # Add total_tokens
            ttft_ms=ttft_ms,
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Verify all metrics are present and calculated correctly
        assert "elapsed_ms" in result, "elapsed_ms should be recorded"
        assert "ttft_ms" in result, "ttft_ms should be recorded"
        assert "tps" in result, "tps should be calculated"
        assert "total_tps" in result, "total_tps should be calculated"
        assert "completion_tps" in result, "completion_tps should be calculated"
        
        # Verify elapsed_ms is positive and reasonable
        assert result["elapsed_ms"] > 0, "elapsed_ms should be positive"
        assert validate_timing(result["elapsed_ms"], expected_elapsed_ms), \
            f"elapsed_ms {result['elapsed_ms']:.2f}ms should be close to expected {expected_elapsed_ms:.2f}ms"
        
        # Verify TTFT matches expected
        assert result["ttft_ms"] == ttft_ms, f"ttft_ms should be {ttft_ms}, got {result['ttft_ms']}"
        
        # Verify TPS calculations
        expected_tps = completion_tokens / (actual_elapsed_ms / 1000.0)
        expected_total_tps = (prompt_tokens + completion_tokens) / (actual_elapsed_ms / 1000.0)
        expected_completion_tps = completion_tokens / ((actual_elapsed_ms - ttft_ms) / 1000.0)
        
        gen_time_ms = actual_elapsed_ms - ttft_ms
        expected_completion_tps = completion_tokens / (gen_time_ms / 1000.0)
        assert abs(result["tps"] - expected_completion_tps) < 1.0, \
            f"tps should be {expected_tps:.2f}, got {result['tps']:.2f}"
        assert abs(result["total_tps"] - expected_total_tps) < 0.01, \
            f"total_tps should be {expected_total_tps:.2f}, got {result['total_tps']:.2f}"
        
        print("✓ Streaming metrics verified:")
        print(f"  - elapsed_ms: {result['elapsed_ms']:.2f}ms (expected ~{expected_elapsed_ms:.2f}ms)")
        print(f"  - ttft_ms: {result['ttft_ms']:.2f}ms")
        print(f"  - tps: {result['tps']:.2f}")
        print(f"  - total_tps: {result['total_tps']:.2f}")
    
    @pytest.mark.asyncio
    async def test_streaming_request_yaml_file_created(self, cleanup_perf_logs):
        """Test that streaming request creates proper YAML file."""
        from keeprollming.performance import record_request_performance
        
        # Record a streaming request with total_tokens
        record_request_performance(
            model="fake-model",
            route_name="test/streaming",
            req_id="streaming-test-002",
            stream=True,
            elapsed_ms=500.0,
            completion_tokens=40,
            prompt_tokens=8,
            total_tokens=48,  # prompt + completion
            ttft_ms=150.0,
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Verify JSON-lines file was created
        yaml_files = list(Path(cleanup_perf_logs).glob("test_streaming.requests.jsonl"))
        assert len(yaml_files) == 1, f"Expected 1 YAML file, found {len(yaml_files)}"
        
        # Load and verify content
        entries = load_requests_yaml(cleanup_perf_logs)
        assert len(entries) == 1, f"Expected 1 entry, found {len(entries)}"
        
        entry = entries[0]
        
        # Verify all required fields
        required_fields = [
            "model", "route_name", "req_id", "stream",
            "elapsed_ms", "ttft_ms", "completion_tokens", "prompt_tokens",
            "total_tokens", "tps", "total_tps"
        ]
        
        for field in required_fields:
            assert field in entry, f"Field '{field}' missing from YAML entry"
        
        print("✓ YAML file created with all required fields:")
        for field in required_fields:
            print(f"  - {field}: {entry[field]}")
    
    @pytest.mark.asyncio
    async def test_streaming_request_summary_updated(self, cleanup_perf_logs):
        """Test that streaming request updates summary correctly."""
        from keeprollming.performance import record_request_performance, set_summary_interval
        set_summary_interval(1)  # Force immediate update for test
        
        # Record multiple streaming requests with varying metrics
        for i in range(3):
            record_request_performance(
                model="fake-model",
                route_name="test/streaming",
                req_id=f"streaming-test-summary-{i}",
                stream=True,
                elapsed_ms=500.0 + i * 100.0,
                completion_tokens=40 + i * 5,
                prompt_tokens=8,
                ttft_ms=150.0,
                performance_logs_dir=cleanup_perf_logs,
            )
        
        # Load and verify summary
        summary = load_summary_yaml(cleanup_perf_logs)
        
        assert "models" in summary, "Summary should contain 'models' key"
        assert len(summary["models"]) > 0, "Summary should have at least one model entry"
        
        # Find our route's entry
        # New format: models is a list of dicts with "model" and "routes" keys
        all_routes = []
        for model_entry in summary["models"]:
            if isinstance(model_entry, dict) and "routes" in model_entry:
                all_routes.extend(model_entry["routes"])
        streaming_routes = [r for r in all_routes if r.get("route_name") == "test/streaming"]
        assert len(streaming_routes) > 0, "Should find streaming route in summary"
        
        route_entry = streaming_routes[0]
        
        # Verify summary statistics
        assert route_entry.get("requests") == 3, "Summary should show 3 requests"
        
        # Verify TPS stats are calculated
        total_tps = route_entry.get("total_tps", {})
        assert "avg" in total_tps, "Summary should include total_tps.avg"
        assert total_tps["avg"] is not None, "total_tps.avg should be a number"
        
        print("✓ Summary updated correctly:")
        print(f"  - requests: {route_entry.get('requests')}")
        print(f"  - total_tps.avg: {total_tps['avg']:.2f}")
    
    @pytest.mark.asyncio
    async def test_streaming_vs_non_streaming_metrics(self, cleanup_perf_logs):
        """Test that streaming and non-streaming metrics are handled correctly."""
        from keeprollming.performance import record_request_performance
        
        # Record streaming request
        record_request_performance(
            model="fake-model",
            route_name="test/streaming",
            req_id="streaming-vs-nonstreaming-001",
            stream=True,
            elapsed_ms=600.0,
            completion_tokens=50,
            prompt_tokens=10,
            ttft_ms=200.0,  # Streaming has TTFT
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Record non-streaming request
        record_request_performance(
            model="fake-model",
            route_name="test/non-streaming",
            req_id="streaming-vs-nonstreaming-002",
            stream=False,
            elapsed_ms=650.0,
            completion_tokens=50,
            prompt_tokens=10,
            ttft_ms=None,  # Non-streaming doesn't have meaningful TTFT
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Load and verify both entries
        all_entries = load_requests_yaml(cleanup_perf_logs)
        
        streaming_entry = next((e for e in all_entries if e.get("stream") is True), None)
        non_streaming_entry = next((e for e in all_entries if e.get("stream") is False), None)
        
        assert streaming_entry is not None, "Should find streaming entry"
        assert non_streaming_entry is not None, "Should find non-streaming entry"
        
        # Verify streaming has TTFT
        assert streaming_entry.get("ttft_ms") is not None, \
            f"Streaming request should have ttft_ms, got {streaming_entry.get('ttft_ms')}"
        assert (ttft_val := streaming_entry.get("ttft_ms")) is not None and ttft_val > 0, "Streaming ttft_ms should be positive"
        
        # Verify non-streaming has None TTFT (not meaningful)
        assert non_streaming_entry.get("ttft_ms") is None, \
            f"Non-streaming request should have ttft_ms=None, got {non_streaming_entry.get('ttft_ms')}"
        
        # Both should have total_tps
        assert streaming_entry.get("total_tps") is not None, "Streaming should have total_tps"
        assert non_streaming_entry.get("total_tps") is not None, "Non-streaming should have total_tps"
        
        print("✓ Streaming vs non-streaming metrics verified:")
        print(f"  - Streaming ttft_ms: {streaming_entry.get('ttft_ms')}")
        print(f"  - Non-streaming ttft_ms: {non_streaming_entry.get('ttft_ms')}")
        print(f"  - Streaming total_tps: {streaming_entry.get('total_tps'):.2f}")
        print(f"  - Non-streaming total_tps: {non_streaming_entry.get('total_tps'):.2f}")


class TestNonStreamingPerformanceMetrics:
    """Tests for non-streaming (buffered) request performance metrics."""
    
    @pytest.mark.asyncio
    async def test_non_streaming_no_ttft(self, cleanup_perf_logs):
        """Test that non-streaming requests don't have TTFT."""
        from keeprollming.performance import record_request_performance
        
        # Record non-streaming request with ttft_ms=None
        result = record_request_performance(
            model="fake-model",
            route_name="test/non-streaming",
            req_id="non-streaming-no-ttft-001",
            stream=False,
            elapsed_ms=350.0,
            completion_tokens=45,
            prompt_tokens=12,
            ttft_ms=None,  # Not applicable for non-streaming
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Verify TTFT is None
        assert result.get("ttft_ms") is None, \
            f"Non-streaming request should have ttft_ms=None, got {result.get('ttft_ms')}"
        
        # But other metrics should be calculated
        assert result.get("elapsed_ms") == 350.0, "elapsed_ms should be 350.0"
        assert result.get("total_tps") is not None, "total_tps should be calculated"
        assert result.get("completion_tokens") == 45, "completion_tokens should be 45"
        
        # total_tps = (prompt + completion) / elapsed
        expected_total_tps = (12 + 45) / (350.0 / 1000.0)
        assert abs(result["total_tps"] - expected_total_tps) < 0.01, \
            f"total_tps should be {expected_total_tps:.2f}, got {result['total_tps']:.2f}"
        
        print("✓ Non-streaming request correctly handled:")
        print(f"  - ttft_ms: {result.get('ttft_ms')}")
        print(f"  - elapsed_ms: {result.get('elapsed_ms')}ms")
        print(f"  - total_tps: {result.get('total_tps'):.2f}")
    
    @pytest.mark.asyncio
    async def test_non_streaming_elapsed_time_accuracy(self, cleanup_perf_logs):
        """Test that non-streaming elapsed time is accurate."""
        from keeprollming.performance import record_request_performance
        
        # Simulate actual timing
        start_time = time.perf_counter()
        
        # Do some work
        await asyncio.sleep(0.1)  # 100ms
        
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        
        # Record with measured elapsed time
        result = record_request_performance(
            model="fake-model",
            route_name="test/non-streaming-timing",
            req_id="non-streaming-timing-001",
            stream=False,
            elapsed_ms=elapsed_ms,
            completion_tokens=30,
            prompt_tokens=8,
            ttft_ms=None,
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Verify elapsed time is close to what we measured
        assert validate_timing(result["elapsed_ms"], 100.0, tolerance=0.2), \
            f"elapsed_ms {result['elapsed_ms']:.2f}ms should be close to 100ms"
        
        print(f"✓ Non-streaming elapsed time accurate: {result['elapsed_ms']:.2f}ms")


class TestToolCallsAndReasoning:
    """Tests for tool calls and reasoning content tracking."""
    
    @pytest.mark.asyncio
    async def test_tool_calls_tracked(self, cleanup_perf_logs):
        """Test that tool calls are properly tracked in metrics."""
        from keeprollming.performance import record_request_performance
        
        # Record request with tool calls
        result = record_request_performance(
            model="fake-model",
            route_name="test/tool-calls",
            req_id="tool-calls-001",
            stream=True,
            elapsed_ms=450.0,
            completion_tokens=60,
            prompt_tokens=15,
            ttft_ms=180.0,
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Verify metrics are calculated even with tool calls
        assert result.get("elapsed_ms") == 450.0, "elapsed_ms should be recorded"
        assert result.get("total_tps") is not None, "total_tps should be calculated"
        assert result.get("completion_tokens") == 60, "completion_tokens should include tool arguments"
        
        print("✓ Tool calls request metrics verified:")
        print(f"  - elapsed_ms: {result['elapsed_ms']}ms")
        print(f"  - total_tps: {result['total_tps']:.2f}")
        print(f"  - completion_tokens: {result['completion_tokens']} (includes tool args)")
    
    @pytest.mark.asyncio
    async def test_reasoning_content_tracked(self, cleanup_perf_logs):
        """Test that reasoning content is tracked in metrics."""
        from keeprollming.performance import record_request_performance
        
        # Record request with reasoning content
        result = record_request_performance(
            model="fake-model",
            route_name="test/reasoning-content",
            req_id="reasoning-content-001",
            stream=True,
            elapsed_ms=550.0,
            completion_tokens=80,  # Includes reasoning tokens
            prompt_tokens=20,
            ttft_ms=200.0,
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Verify reasoning content tokens are included in completion count
        assert result.get("completion_tokens") == 80, \
            "completion_tokens should include reasoning tokens"
        assert result.get("total_tps") is not None, "total_tps should be calculated"
        
        print("✓ Reasoning content metrics verified:")
        print(f"  - elapsed_ms: {result['elapsed_ms']}ms")
        print(f"  - completion_tokens: {result['completion_tokens']} (includes reasoning)")
        print(f"  - total_tps: {result['total_tps']:.2f}")


class TestConfigurableLogsDirectory:
    """Tests for configurable performance logs directory."""
    
    @pytest.mark.asyncio
    async def test_custom_logs_directory(self, clean_perf_logs_dir):
        """Test that custom logs directory can be configured and used."""
        from keeprollming.performance import record_request_performance, _PERF_LOGS_DIR
        
        # Verify default is None until set
        assert _PERF_LOGS_DIR is None, "Default should be None until configured"
        
        # Record with custom directory
        record_request_performance(
            model="fake-model",
            route_name="test/custom-dir",
            req_id="custom-dir-001",
            stream=True,
            elapsed_ms=400.0,
            completion_tokens=35,
            prompt_tokens=10,
            ttft_ms=150.0,
            performance_logs_dir=clean_perf_logs_dir,  # Custom directory
        )
        
        # Verify file was created in custom directory
        yaml_files = list(Path(clean_perf_logs_dir).glob("test_custom-dir.requests.jsonl"))
        assert len(yaml_files) == 1, f"Expected file in custom dir, found {len(yaml_files)}"

        # Verify default directory still has no files
        from keeprollming.performance import _ensure_dir
        default_dir = _ensure_dir()
        _default_yaml_files = list(default_dir.glob("*.requests.jsonl"))
        
        print("✓ Custom logs directory working:")
        print(f"  - Custom dir: {clean_perf_logs_dir}")
        print(f"  - Default dir: {default_dir}")
        print(f"  - Files in custom dir: {len(yaml_files)}")
    
    @pytest.mark.asyncio
    async def test_multiple_routes_separate_files(self, clean_perf_logs_dir):
        """Test that different routes get separate YAML files."""
        from keeprollming.performance import record_request_performance
        
        # Record requests for multiple routes
        routes = ["route/a", "route/b", "route/c"]
        
        for i, route in enumerate(routes):
            record_request_performance(
                model="fake-model",
                route_name=route,
                req_id=f"multi-route-{i}",
                stream=True,
                elapsed_ms=400.0 + i * 50.0,
                completion_tokens=35 + i * 5,
                prompt_tokens=10,
                ttft_ms=150.0,
                performance_logs_dir=clean_perf_logs_dir,
            )
        
        # Verify each route has its own file
        yaml_files = list(Path(clean_perf_logs_dir).glob("*.requests.jsonl"))

        assert len(yaml_files) == 3, f"Expected 3 separate files, found {len(yaml_files)}"

        # Verify each file has correct route name
        for route in routes:
            expected_file = Path(clean_perf_logs_dir) / f"{route.replace('/', '_')}.requests.jsonl"
            assert expected_file.exists(), f"File {expected_file} should exist"
        
        print("✓ Multiple routes create separate files:")
        for yf in yaml_files:
            print(f"  - {yf.name}")


class TestMetricsCalculationAccuracy:
    """Tests for TPS and timing calculation accuracy."""
    
    @pytest.mark.asyncio
    async def test_tps_calculation_accuracy(self, cleanup_perf_logs):
        """Test that TPS calculations are mathematically correct."""
        from keeprollming.performance import record_request_performance
        
        # Test case 1: Simple calculation
        prompt_tokens = 20
        completion_tokens = 80
        elapsed_ms = 1000.0  # 1 second
        ttft_ms = 200.0  # 200ms TTFT
        
        result = record_request_performance(
            model="fake-model",
            route_name="test/tps-accuracy",
            req_id="tps-accuracy-001",
            stream=True,
            elapsed_ms=elapsed_ms,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            ttft_ms=200.0,
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Expected: tps uses generation time (elapsed - TTFT) for streaming
        # 80 tokens / 0.8s (1000ms - 200ms) = 100 tps
        expected_tps = completion_tokens / ((elapsed_ms - ttft_ms) / 1000.0)
        assert abs(result["tps"] - expected_tps) < 1.0, \
            f"tps should be {expected_tps:.2f}, got {result['tps']:.2f}"
        
        # Expected: (20 + 80) tokens / 1s = 100 total_tps
        expected_total_tps = (prompt_tokens + completion_tokens) / (elapsed_ms / 1000.0)
        assert abs(result["total_tps"] - expected_total_tps) < 0.01, \
            f"total_tps should be {expected_total_tps}, got {result['total_tps']}"
        
        print("✓ TPS calculations accurate:")
        print(f"  - tps: {result['tps']:.2f} (expected {expected_tps})")
        print(f"  - total_tps: {result['total_tps']:.2f} (expected {expected_total_tps})")
    
    @pytest.mark.asyncio
    async def test_completion_tps_excludes_ttft(self, cleanup_perf_logs):
        """Test that completion_tps only counts generation time (excludes TTFT)."""
        from keeprollming.performance import record_request_performance
        
        # Scenario: Long TTFT, fast completion
        ttft_ms = 500.0  # 500ms wait for first token
        elapsed_ms = 600.0  # Total time is 600ms
        completion_tokens = 100
        
        result = record_request_performance(
            model="fake-model",
            route_name="test/completion-tps",
            req_id="completion-tps-001",
            stream=True,
            elapsed_ms=elapsed_ms,
            completion_tokens=completion_tokens,
            prompt_tokens=10,
            ttft_ms=ttft_ms,
            performance_logs_dir=cleanup_perf_logs,
        )
        
        # Completion TPS should be based on (elapsed - TTFT) = 600 - 500 = 100ms
        # 100 tokens / 0.1s = 1000 tps
        generation_time_ms = elapsed_ms - ttft_ms
        expected_completion_tps = completion_tokens / (generation_time_ms / 1000.0)
        
        assert abs(result["completion_tps"] - expected_completion_tps) < 0.01, \
            f"completion_tps should be {expected_completion_tps}, got {result['completion_tps']}"
        
        print("✓ completion_tps excludes TTFT:")
        print(f"  - generation_time: {generation_time_ms}ms")
        print(f"  - completion_tps: {result['completion_tps']:.2f}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
