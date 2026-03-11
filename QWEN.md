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

### Key Components

1. **FastAPI Application (`keeprollming/app.py`)**:
   - Handles incoming requests, processes them through the orchestrator logic, and sends responses back to the client.

2. **Profiles**:
   - Defined in `keeprollming/config.py` using a dataclass.
   - Supports different profiles like `local/quick`, `local/main`, and `local/deep`.

3. **Orchestrator Logic**:
   - Handles token counting, message splitting, and summarization as needed.

4. **Upstream Client (`keeprollming/upstream.py`)**:
   - Manages communication with the OpenAI-compatible backend using `httpx.AsyncClient`.

5. **Testing Framework**:
   - Uses `pytest` for testing.
   - Mocks upstream calls in tests to avoid live LM Studio instances.

6. **Configuration Management**:
   - Environment variables like `UPSTREAM_BASE_URL`, `MAIN_MODEL`, etc., are used to configure the application.

## Key Implementation Details

### Rolling Summary Algorithm
The orchestrator uses a rolling summary approach where it:
1. Identifies when the conversation history needs summarization based on token count and context length
2. Uses either classic head/middle/tail or cache-append strategies for summarizing
3. Maintains an incremental caching system to avoid reprocessing already summarized content
4. Supports streaming responses through SSE (Server-Sent Events)

### Summary Caching
- Cache entries are stored in `./summary_cache` directory by default
- Entries are indexed by conversation fingerprint and range hash
- When a reusable checkpoint is found, the orchestrator prefers incremental reuse (`existing summary + delta`) instead of regenerating the whole middle from scratch
- Failed / placeholder summaries are skipped for cache save

### Profile Management
The system supports three profiles:
- `quick`: Uses less resource-intensive models for faster responses
- `main`: Default profile with balanced performance and quality
- `deep`: Uses larger, more capable models for complex tasks

## Recent Summary/Cache Updates

- Cache retrieval now matches reusable checkpoints by the current summary start index and validates the saved range hash only on the covered prefix.
- When a reusable checkpoint is found, the orchestrator prefers incremental reuse (`existing summary + delta`) instead of regenerating the whole middle from scratch.
- Failed / placeholder summaries are skipped for cache save.
- The default curated summary prompt now asks for compact YAML output to reduce template leakage and make the compressed context more stable across turns.
- Extra logs were added for cache candidate rejection and incremental reuse (`summary_cache_candidate_rejected`, `summary_incremental_reuse`, `summary_cache_skip_save`).

## Development Conventions

### Testing
Tests are structured with:
- Unit/integration tests in `tests/test_orchestrator.py`
- End-to-end tests in `tests/e2e/`
- Regression tests for summary overflow scenarios in `tests/test_summary_overflow_regression.py`

### Code Style
- Uses dataclasses for configuration management
- Leverages FastAPI for web framework
- Implements async/await pattern for non-blocking operations
- Follows Python best practices and PEP8 style guide

### Logging
The system supports multiple logging levels:
- INFO: General operational information
- WARN: Warnings about issues that don't stop execution
- ERROR: Errors that cause failures or fallback behavior
- DEBUG: Detailed debugging information (for development only)

## Workflow Guidelines

For project workflow conventions and task management, please refer to [WORKFLOW.md](./workflow/WORKFLOW.md) which contains detailed guidelines on:
- Active work tracking via CURRENT-TASK.md
- Completed tasks archival in TASK-HISTORY.md  
- Future task planning in TODO.md
- Overall project collaboration conventions

## Project Guidelines

For project-level documentation and conventions, please refer to [PROJECT.md](./project/PROJECT.md) which contains detailed information about:
- Overall project overview 
- Versioning strategy and practices
- Coding conventions, best practices, etc.
- Configuration management guidelines
