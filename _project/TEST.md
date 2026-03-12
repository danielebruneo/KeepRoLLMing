# Testing Guidelines for Keeprollming Orchestrator

## Test Structure

The project uses pytest for testing with the following structure:
- `tests/test_orchestrator.py` - Unit/integration tests for the orchestrator core functionality
- `tests/test_summary_overflow_regression.py` - Regression tests for summary overflow scenarios
- `tests/e2e/` - End-to-end tests directory

## Test Execution

Install dev requirements:

```bash
pip install -r requirements-dev.txt
```

Run:

```bash
pytest
```

## Test Categories

### Unit/Integration Tests
- Most tests in `test_orchestrator.py`
- These tests mock upstream calls to avoid requiring a live LM Studio instance
- They cover core orchestrator functionality including:
  - Passthrough mode routing without summarization
  - Rolling summary triggering and repacking logic  
  - Streaming proxy (SSE) support
  - Token accounting and caching mechanisms

### End-to-end Tests 
- Located in `tests/e2e/` directory
- These tests can connect to a real LM Studio backend when configured via `live_backend_config.sh`
- They test the full integration with actual LLM models

## Test Optimization 

The project has already implemented performance optimization:
- Reduced test execution time from 36s to 11s using pytest-xdist parallelization
- Configuration includes `-n auto` flag for parallel execution where possible

## Testing Considerations

### Mocking Strategy
- Upstream calls are mocked in most tests to avoid live backend requirements
- Test fixtures setup common mocking patterns for efficient test runs

### Parallel Execution
- Tests support parallel execution via pytest-xdist plugin
- Some end-to-end tests may have issues with parallelization due to test infrastructure limitations