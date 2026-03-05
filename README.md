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

## Aider tips

See `AIDER.md` for a small set of rules that helps keep patches small, tests deterministic,
and avoids the "duplicate-tests explosion".
