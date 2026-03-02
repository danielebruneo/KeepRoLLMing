#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate

export LOG_LEVEL=INFO
export LOG_JSON=1

export DEBUG_ENABLE_ENDPOINTS=1
export DEBUG_UPSTREAM=1
export DEBUG_UPSTREAM_BODY_CHARS=2000

# se stai bypassando LiteLLM e punti a LM Studio:
export LMSTUDIO_BASE="http://127.0.0.1:1234"
export LITELLM_BASE="http://127.0.0.1:1234/v1"
export MAIN_MODEL="qwen2.5-3b-instruct"
export SUMMARY_MODEL="qwen2.5-1.5b-instruct"
export LM_MAIN_ID="qwen2.5-3b-instruct"
export LM_SUMMARY_ID="qwen2.5-1.5b-instruct"


uvicorn orchestrator:app --host 0.0.0.0 --port 8000 --log-level debug