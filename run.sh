#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# attiva venv
source .venv/bin/activate

# =========================
# LOGGING
# =========================
export LOG_LEVEL="DEBUG"
#export LOG_LEVEL="MEDIUM"
#export LOG_LEVEL="BASIC"
#export LOG_LEVEL="BASIC_PLAIN"

export LOG_JSON=1

# =========================
# BACKEND LLM (LM Studio)
# =========================
export UPSTREAM_BASE_URL="http://arkai.local:1234"
export UPSTREAM_BASE_URL="http://arkai.local:8000/api"
#export MAIN_MODEL="qwen2.5-7b-instruct-uncensored"

export QUICK_MAIN_MODEL="qwen2.5-3b-instruct"
export QUICK_SUMMARY_MODEL="qwen2.5-1.5b-instruct"

export BASE_MAIN_MODEL="qwen2.5-vl-7b-instruct"
export BASE_SUMMARY_MODEL="qwen2.5-3b-instruct"

export DEEP_MAIN_MODEL="qwen/qwen3.5-35b-a3b"
export DEEP_SUMMARY_MODEL="qwen2.5-3b-instruct"


export DEEP_MAIN_MODEL="DeepSeek-R1-0528-Qwen3-8B-FLM"
export DEEP_SUMMARY_MODEL="Llama-3.2-3B-Instruct-GGUF"


# =========================
# CONTEXT SETTINGS
# =========================

# limite hard del modello (puoi aumentare se il modello lo supporta)
export DEFAULT_CTX_LEN=4000
export SUMMARY_MAX_TOKENS=512
export SAFETY_MARGIN_TOK=300

export MAX_HEAD=5
export MAX_TAIL=5
export SUMMARY_PROMPT_DIR=./prompts
export SUMMARY_PROMPT_TYPE=curated
export SUMMARY_TEMPERATURE=0.2
export SUMMARY_MODE="cache_append"



# =========================
# RUN SERVER
# =========================
trap 'echo "ByeBye"' INT

python keeprollming.py

#uvicorn keeprollming_orchestrator:app \
#  --host 0.0.0.0 \
#  --port 8000 \
#  --log-level info
