# Orchestrator v3.6 — overflow-only summary + token accounting + tok/s metrics

## What changed
- Removed proactive summary triggers. Summary happens ONLY on overflow.
- Streaming proxy now captures the assistant output to update internal STATE.
- Adds token accounting logs (estimated) and tok/s:
  - main: tok_s_est and (best-effort) tok_s_backend if backend returns usage in stream/non-stream
  - summary: tok_s_est and (best-effort) tok_s_backend if backend returns usage

## Key logs
- budget: shows ctx_eff, input/out budgets, current prompt token estimate, and whether summarization happened.
- summary_trigger: emitted right before calling summary model (reason=overflow).
- summary_call: summary timing + token/sec metrics.
- summary_applied: new summary/tail sizes after summary.
- main_stream_done / main_completion: tok/sec metrics + backend usage if available.

## Backend usage availability
To get usage in streaming, LibreChat typically sends:
  "stream_options": {"include_usage": true}
If LM Studio returns usage in SSE, v3.6 will log `usage` and compute `tok_s_backend`.

## Recommended env (direct to LM Studio)
export LITELLM_BASE=http://127.0.0.1:1234/v1
export MAIN_MODEL=qwen2.5-3b-instruct
export SUMMARY_MODEL=qwen2.5-1.5b-instruct
export DEBUG_ENABLE_ENDPOINTS=1
export LOG_JSON=1
