#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

source .venv/bin/activate

# =========================
# LOGGING
# =========================
export LOG_LEVEL="DEBUG"
# export LOG_LEVEL="MEDIUM"
# export LOG_LEVEL="BASIC"
# export LOG_LEVEL="BASIC_PLAIN"
export LOG_JSON=1

# =========================
# BACKEND LLM
# =========================
# Base URL of your OpenAI-compatible backend.
# Use exactly one value here.
export UPSTREAM_BASE_URL="http://arkai.local:8000/api"
# export UPSTREAM_BASE_URL="http://arkai.local:1234"

export QUICK_MAIN_MODEL="qwen2.5-3b-instruct"
export QUICK_SUMMARY_MODEL="qwen2.5-1.5b-instruct"

export BASE_MAIN_MODEL="qwen2.5-vl-7b-instruct"
export BASE_SUMMARY_MODEL="qwen2.5-3b-instruct"

export DEEP_MAIN_MODEL="DeepSeek-R1-0528-Qwen3-8B-FLM"
export DEEP_SUMMARY_MODEL="Llama-3.2-3B-Instruct-GGUF"

# =========================
# CONTEXT / SUMMARY
# =========================
export DEFAULT_CTX_LEN=4000
export SUMMARY_MAX_TOKENS=512
export SAFETY_MARGIN_TOK=300

export MAX_HEAD=5
export MAX_TAIL=5
export SUMMARY_PROMPT_DIR=./prompts
export SUMMARY_PROMPT_TYPE=curated
export SUMMARY_TEMPERATURE=0.2

# cache_append:
# - preserve base system prompt raw
# - preserve first user prompt raw
# - preserve tail raw window
# - reuse cached compact context when possible
# - rebuild summary incrementally only when needed
export SUMMARY_MODE="cache_append"
export SUMMARY_CACHE_ENABLED=1
export SUMMARY_CACHE_DIR=./summary_cache
export SUMMARY_CACHE_FINGERPRINT_MSGS=1
export SUMMARY_CONSOLIDATE_WHEN_NEEDED=1
export SUMMARY_FORCE_CONSOLIDATE=0

trap 'echo "ByeBye"' INT
python keeprollming.py
