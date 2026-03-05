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

export QUICK_MAIN_MODEL="qwen2.5-3b-instruct"
export QUICK_SUMMARY_MODEL="qwen2.5-1.5b-instruct"

export BASE_MAIN_MODEL="qwen2.5-vl-7b-instruct"
export BASE_SUMMARY_MODEL="qwen2.5-3b-instruct"

export DEEP_MAIN_MODEL="qwen/qwen3.5-35b-a3b"
export DEEP_SUMMARY_MODEL="qwen2.5-3b-instruct"



# =========================
# CONTEXT SETTINGS
# =========================

# limite hard del modello (puoi aumentare se il modello lo supporta)
export DEFAULT_CTX_LEN=4096
export SUMMARY_MAX_TOKENS=256
export SAFETY_MARGIN_TOK=128
export LOG_LEVEL="DEBUG"
export LOG_LEVEL="INFO"
export LOG_LEVEL="BASIC"


# =========================
# RUN SERVER
# =========================
trap 'echo "ByeBye"' INT

python keeprollming.py

#uvicorn keeprollming_orchestrator:app \
#  --host 0.0.0.0 \
#  --port 8000 \
#  --log-level info
