# Commands

## Setup and Run

```bash
# Setup virtual environment and install dependencies
./setup.sh

# Run the application with default settings
./run.sh

# Alternative: run directly with uvicorn
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000
```

## Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run all tests in serial mode (recommended)
./run-tests.sh

# Run a single test
./run-single-test.sh test_name

# Run tests in parallel mode (may have issues with some tests)
./run-parallel-tests.sh

# Run live E2E tests against a configured backend
./live-test.sh

# Run curl-based tests
./run-curl-tests.sh
```

## Environment Configuration

```bash
# Main configuration variables (from config.yaml and environment)
export UPSTREAM_BASE_URL="http://arkai.local:1234"
export QUICK_MAIN_MODEL="qwen2.5-3b-instruct"
export QUICK_SUMMARY_MODEL="qwen2.5-1.5b-instruct"
export BASE_MAIN_MODEL="qwen2.5-vl-7b-instruct"
export BASE_SUMMARY_MODEL="qwen2.5-3b-instruct"
export DEEP_MAIN_MODEL="qwen3.5-4b-uncensored-hauhaucs-aggressive"
export DEEP_SUMMARY_MODEL="qwen2.5-3b-instruct"
export DEFAULT_CTX_LEN=8000
export SUMMARY_MAX_TOKENS=1000
export SAFETY_MARGIN_TOK=1000
export MAX_HEAD=3
export MAX_TAIL=3
export SUMMARY_PROMPT_DIR=./_prompts
export SUMMARY_PROMPT_TYPE=curated
export SUMMARY_TEMPERATURE=0.3
export SUMMARY_MODE="cache_append"
export SUMMARY_CACHE_ENABLED=true
export SUMMARY_CACHE_DIR="./__summary_cache"
```