# Commands

## Run Application

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export UPSTREAM_BASE_URL="http://127.0.0.1:1234"   # LM Studio base (no /v1)
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000
```

## Test Commands

Install dev requirements:
```bash
pip install -r requirements-dev.txt
```

Run all tests:
```bash
pytest
```

Run with specific configuration:
```bash
pytest --tb=no -n0
```

Use dedicated test scripts (recommended):
```bash
./run-tests.sh          # Run all tests in serial mode
./run-single-test.sh    # Run a single test
./run-parallel-tests.sh # Run tests in parallel mode
```

## Live Testing

For end-to-end tests against a real LM Studio backend:
1. Set up your live backend configuration in `live_backend_config.sh`
2. Run: `./live-test.sh`

## Configuration Environment Variables

- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `SUMMARY_MODE` (default `cache_append`)
- `SUMMARY_CACHE_ENABLED` (default `true`)
- `SUMMARY_CACHE_DIR` (default `./__summary_cache`)