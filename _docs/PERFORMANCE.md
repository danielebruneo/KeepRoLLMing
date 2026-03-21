# Performance Documentation

## Overview
This document contains performance metrics, analysis, and optimization details for the Keeprollming orchestrator.

## Monitoring Tools

### Terminal Dashboard (`perf_dashboard.py`)
Real-time terminal dashboard for monitoring model performance metrics.

**Features:**
- Live display of **Tot TPS** (total throughput), completion_tps, prompt_tps, TTFT, token counts
- Model-by-model breakdown with sorting by request count
- Interactive controls: `q` (quit), `c` (clear logs), `s` (save backup)
- Automatic refresh on summary.yaml changes

**Usage:**
```bash
python perf_dashboard.py                    # Auto-detects PERFORMANCE_LOGS_DIR
python perf_dashboard.py /path/to/summary   # Specify custom path
```

**Data Source:**
- Reads from `summary.yaml` in `__performance_logs/` directory
- Backup directory created automatically: `__performance_logs/backups/`

### Summary Generation (`keeprollming/performance.py`)
The `_update_summary()` function generates `summary.yaml` by aggregating performance data from individual request logs. It calculates statistics (avg, min, max) for all metrics including the new `total_tps` field.

**Metrics in summary.yaml:**
- `tps` - Completion TPS (backward compatibility alias)
- `total_tps` - Overall throughput metric
- `completion_tps` - Generation speed
- `prompt_tps` - Prompt processing speed
- Token counts and TTFT statistics

### Benchmarking Tool (`benchmark_routes.py`)
Route benchmarking for measuring performance across different configurations.

**Usage:**
```bash
python benchmark_routes.py --num-prompts 5 --filter "chat/main"
```

**Arguments:**
- `--num-prompts`: Number of iterations per route
- `--filter`: Pattern filter (e.g., "chat/*", "code/senior")
- Output groups by backend_model for easier comparison

## Test Suite Performance

### Current Status
- Test execution time reduced from 36s to under 10s via parallelization
- All tests pass with default pytest configuration (`pytest --tb=no -x`)

### Optimization Achievements
- Implemented pytest-xdist plugin for parallel execution
- Configured `-n auto` flag in pytest.ini for automatic parallelization
- Marked problematic tests as `non_parallelizable` to maintain functionality

## Performance Metrics

### Key Metrics Tracked:
- Test execution time (reduced from 36s to ~10s)
- Context handling efficiency
- Streaming response times
- Token accounting accuracy
- TTFT (Time to First Token)
- **TPS metrics:**
  - `total_tps` - Overall throughput: `(prompt_tokens + completion_tokens) / elapsed_time`
  - `completion_tps` - Generation speed: `completion_tokens / (elapsed_time - ttft)`
  - `prompt_tps` - Prompt processing speed: `prompt_tokens / ttft`
- Token counts for both input and output

### How total_tps is Calculated:
The `total_tps` metric represents end-to-end throughput, calculated as:

```python
total_tps = (prompt_tokens + completion_tokens) / (elapsed_ms / 1000.0)
```

This measures the total number of tokens processed per second from request start to finish, including both prompt processing and completion generation time. It's the standard metric for overall system throughput.

**Example:** If a request takes 5 seconds and processes 30,000 prompt tokens + 400 completion tokens:
- `total_tps = (30000 + 400) / 5 = 6080 tokens/second`

### Optimization Techniques:
1. Parallel test execution using pytest-xdist
2. Strategic non_parallelizable marking for shared resource tests
3. Efficient caching mechanisms in `__summary_cache.py`
4. Real-time dashboard for performance monitoring

## Resource Usage Analysis

### Memory Efficiency
- Rolling summary algorithm designed to minimize memory overhead
- Cache system optimized for reuse patterns

### Computational Efficiency
- Token counting with fallback logic
- Streaming proxy implementation without excessive processing delays

## Future Performance Improvements

### Areas for Optimization:
1. Further test suite parallelization where possible
2. Enhanced caching strategies
3. More efficient token counting algorithms
4. Improved streaming response handling
5. Dashboard performance optimizations (reduce rendering overhead)

### Monitoring Requirements:
- Regular performance regression testing
- Continuous monitoring of resource usage patterns
- Periodic backup of summary data using dashboard `s` key