# Performance Documentation

## Overview
This document contains performance metrics, analysis, and optimization details for the Keeprollming orchestrator.

## Monitoring Tools

### Terminal Dashboard (`perf_dashboard.py`)
Real-time terminal dashboard for monitoring model performance metrics.

**Features:**
- Live display of TPS, TTFT, completion tokens, prompt tokens
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
- TPS (Tokens Per Second) - overall, completion, and prompt-specific
- Token counts for both input and output

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