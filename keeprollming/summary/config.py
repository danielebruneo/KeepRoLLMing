"""Configuration constants for the summary module."""

import os

# ---------------------------------------------------------------------
# Prompt configuration
# ---------------------------------------------------------------------
SUMMARY_PROMPT_DIR = os.getenv("SUMMARY_PROMPT_DIR", "./_prompts")
SUMMARY_PROMPT_TYPE = os.getenv("SUMMARY_PROMPT_TYPE", "curated")
SUMMARY_TEMPERATURE = float(os.getenv("SUMMARY_TEMPERATURE", "0.2"))

# ---------------------------------------------------------------------
# Retry & backend limits
# ---------------------------------------------------------------------
MAX_SUMMARY_BACKEND_ATTEMPTS = int(os.getenv("MAX_SUMMARY_BACKEND_ATTEMPTS", "8"))

# ---------------------------------------------------------------------
# Head/Tail selection for repacking
# ---------------------------------------------------------------------
MAX_HEAD = int(os.getenv("MAX_HEAD", "3"))
MAX_TAIL = int(os.getenv("MAX_TAIL", "3"))
SUMMARY_PIN_FIRST_USER = os.getenv("SUMMARY_PIN_FIRST_USER", "1").strip().lower() not in {"0", "false", "no", "off"}

# ---------------------------------------------------------------------
# Token budgeting
# ---------------------------------------------------------------------
from ..config import SAFETY_MARGIN_TOK, SUMMARY_MAX_TOKENS  # noqa: E402

SUMMARY_INSERT_BUDGET_TOK = int(os.getenv("SUMMARY_INSERT_BUDGET_TOK", str(SUMMARY_MAX_TOKENS)))
