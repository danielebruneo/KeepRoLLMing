# Keeprollming Orchestrator

A small FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio)
and adds **rolling-summary** support to avoid context overflow.

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

## Run

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
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'
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

- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1` is accepted, but recommended to provide without `/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)

## Key components

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

### Example Usage

Here's an example of how you might use the Keeprollming Orchestrator:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export UPSTREAM_BASE_URL="http://127.0.0.1:1234/v1"   # LM Studio base (no /v1)
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000
```

Then, you can call the API:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'
```

### Testing

To run the tests, install dev requirements and execute:

```bash
pip install -r requirements-dev.txt
pytest
```

This setup ensures that the project is well-structured, with clear documentation and a robust testing framework to maintain code quality.

## Recent summary/cache updates

- Cache retrieval now matches reusable checkpoints by the current summary start index and validates the saved range hash only on the covered prefix.
- When a reusable checkpoint is found, the orchestrator prefers incremental reuse (`existing summary + delta`) instead of regenerating the whole middle from scratch.
- Failed / placeholder summaries are skipped for cache save.
- The default curated summary prompt now asks for compact YAML output to reduce template leakage and make the compressed context more stable across turns.
- Extra logs were added for cache candidate rejection and incremental reuse (`summary_cache_candidate_rejected`, `summary_incremental_reuse`, `summary_cache_skip_save`).
