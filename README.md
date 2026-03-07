# Keeprollming Orchestrator

A small FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (LM Studio, llama.cpp server, Lemonade, etc.) and adds rolling-summary support to avoid context overflow.

## Features

- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- Profiles:
  - `local/quick`
  - `local/main`
  - `local/deep`
- Passthrough mode:
  - `pass/<BACKEND_MODEL_NAME>`
- Streaming proxy (SSE) support
- Summary cache on disk
- Best-effort token accounting
- Plain-text and structured logging modes

## Rolling summary flow

The default summary mode is `cache_append`.

When the prompt would exceed the main model budget, the orchestrator tries to preserve the conversation like this:

1. Keep the base `system` prompt raw.
2. Keep the first `user` message raw.
3. Keep the recent tail raw.
4. Compress only the middle portion into `[ARCHIVED_COMPACT_CONTEXT]`.
5. Reuse a cached compact context from disk when possible.
6. Rebuild the compact context incrementally only when needed.

This avoids relying entirely on the summary model for foundational prompt instructions.

## Summary cache flow

The cache stores compact summaries on disk and tries to match them back to the same conversation.

Priority order:

- LibreChat headers when available:
  - `x-librechat-user-id`
  - `x-librechat-conversation-id`
- Fallback fingerprint when those headers are not available.

The cache stores which non-system range was compacted and validates that range against the current message history before reusing it.

## Oversized summary requests

If the summary request itself would exceed the summary model context, the orchestrator now handles it in two ways:

- **Preflight check**: estimate the summary prompt size before sending it upstream.
- **Backend overflow detection**: if the backend returns a JSON error payload such as `request (...) exceeds the available context size (...)`, chunking is triggered automatically.

For large conversations, the orchestrator chunks the summary input, produces partial summaries, then merges them into a final compact context.

The same logic also applies to incremental summary rebuilds.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000
```

Or use the bundled launcher:

```bash
bash run.sh
```

Then call:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'
```

## Main configuration

### Backend

- `UPSTREAM_BASE_URL`
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`

### Context / summary

- `DEFAULT_CTX_LEN`
- `SUMMARY_MAX_TOKENS`
- `SAFETY_MARGIN_TOK`
- `MAX_HEAD`, `MAX_TAIL`
- `SUMMARY_MODE`
- `SUMMARY_TEMPERATURE`
- `SUMMARY_PROMPT_DIR`
- `SUMMARY_PROMPT_TYPE`

### Summary cache

- `SUMMARY_CACHE_ENABLED`
- `SUMMARY_CACHE_DIR`
- `SUMMARY_CACHE_FINGERPRINT_MSGS`
- `SUMMARY_CONSOLIDATE_WHEN_NEEDED`
- `SUMMARY_FORCE_CONSOLIDATE`

## Notes

- `MAX_HEAD` / `MAX_TAIL` still matter for classic rolling summary planning.
- In `cache_append`, the effective preservation policy is:
  - base `system` raw
  - first `user` raw
  - tail raw window
  - compacted middle
- Outgoing messages are flattened to plain text before being sent upstream, so multimodal-style `content: [{type:"text", ...}]` blocks do not break prompt templates expecting a plain user string.

## Tests

```bash
pytest
```

The tests mock upstream calls; no live LM Studio instance is required.
