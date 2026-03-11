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

For running instructions, please refer to [RUNNING.md](./docs/RUNNING.md).

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

For configuration details, please refer to [CONFIGURATION.md](./docs/CONFIGURATION.md).

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
