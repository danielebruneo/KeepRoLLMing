#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# attiva venv
source .venv/bin/activate

# =========================
# LOGGING
# =========================
export LOG_LEVEL=INFO
export LOG_JSON=1

# =========================
# BACKEND LLM (LM Studio)
# =========================
export UPSTREAM_BASE_URL="http://arkai.local:1234"
#export MAIN_MODEL="qwen2.5-7b-instruct-uncensored"
export MAIN_MODEL="qwen2.5-3b-instruct"
export SUMMARY_MODEL="qwen2.5-1.5b-instruct"

# =========================
# CONTEXT SETTINGS
# =========================

# limite hard del modello (puoi aumentare se il modello lo supporta)
export DEFAULT_CTX_LEN=4096
export SUMMARY_MAX_TOKENS=256
export SAFETY_MARGIN_TOK=128

# =========================
# RUN SERVER
# =========================
uvicorn keeprollming_orchestrator:app \
  --host 0.0.0.0 \
  --port 8000 \
  --log-level info