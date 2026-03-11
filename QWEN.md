# Keeprollming Orchestrator

A small FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds **rolling-summary** support to avoid context overflow.

## Features

- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- Profiles:
  - `local/quick`
  - `local/main`
  - `local/deep`
- Passthrough mode:
  - `pass/<BACKEND_MODEL_NAME>` (routes directly, **no summarization**)
- Streaming proxy (SSE) support
- Best-effort token accounting

## Project Structure

```
/home/daniele/LLM/orchestrator/
├── README.md          # Main documentation
├── keeprollming.py    # Entry point for running the application
├── keeprollming/      # Main source code directory
│   ├── app.py         # FastAPI application implementation
│   ├── config.py      # Configuration management and profile definitions
│   ├── upstream.py    # Communication with OpenAI-compatible backend
│   ├── rolling_summary.py  # Logic for summarizing conversation history
│   ├── summary_cache.py    # Summary caching functionality
│   ├── token_counter.py    # Token counting utilities
│   └── logger.py      # Logging and debugging helpers
├── tests/             # Test directory
│   ├── test_orchestrator.py  # Unit/integration tests for the orchestrator
│   ├── test_summary_overflow_regression.py  # Regression tests
│   └── e2e/           # End-to-end tests
├── requirements.txt   # Production dependencies
├── requirements-dev.txt  # Development/test dependencies
└── run.sh             # Script to start the application
```

## Running

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export UPSTREAM_BASE_URL="http://127.0.0.1:1234"   # LM Studio base (no /v1)
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000
```

Then call:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'}'
```

## Tests

Install dev requirements:

```bash
pip install -r requirements-dev.txt
```

Run:

```bash
pytest
```

Notes:
- Tests are **unit/integration-ish** but do not require a live LM Studio instance: upstream calls are mocked.

## Configuration

### Environment Variables

- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1` is accepted, but recommended to provide without `/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `DEFAULT_CTX_LEN` - Default context length when no model info is available
- `SUMMARY_MAX_TOKENS` - Maximum tokens for summary generation

## Development Conventions

The project follows specific conventions and best practices which are documented in [CONVENTIONS.md](./project/CONVENTIONS.md).

## Workflow Guidelines

For project workflow conventions and task management, please refer to [WORKFLOW.md](./workflow/WORKFLOW.md) which contains detailed guidelines on:
- Active work tracking via ACTIVE_TASK.md
- Completed tasks archival in COMPLETED_TASKS.md
- Future task planning in TODO.md
- Overall project collaboration conventions

## Project Guidelines

For project-level documentation and conventions, please refer to [PROJECT.md](./project/PROJECT.md) which contains detailed information about:
- Overall project overview
- Versioning strategy and practices
- Coding conventions, best practices, etc.
- Configuration management guidelines
