#!/usr/bin/env bash
set -euo pipefail

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
source .venv/bin/activate

# =========================
# LOGGING
# =========================
export LOG_LEVEL="DEBUG"
# export LOG_LEVEL="MEDIUM"
# export LOG_LEVEL="BASIC"
# export LOG_LEVEL="BASIC_PLAIN"

export LOG_JSON=1
# Set to 0 to show full raw response bodies (no truncation)
export LOG_SNIP_CHARS=0

# =========================
# CONFIGURATION FILE
# =========================
# Use config.yaml for all settings (recommended)
CONFIG_FILE="./config.yaml"

# Optional: Override specific settings via environment variables if needed
# These will be read by the application from config.yaml, not here
export DEFAULT_CTX_LEN=8000
export SUMMARY_MAX_TOKENS=1000
export SAFETY_MARGIN_TOK=1000

# =========================
# CONTEXT SETTINGS
# =========================
export MAX_HEAD=3
export MAX_TAIL=3
export SUMMARY_PROMPT_DIR=./_prompts
export SUMMARY_PROMPT_TYPE=curated
export SUMMARY_TEMPERATURE=0.3
export SUMMARY_MODE="cache_append"

# =========================
# RUN SERVER
# =========================
trap 'echo "ByeBye"' INT

python keeprollming.py
