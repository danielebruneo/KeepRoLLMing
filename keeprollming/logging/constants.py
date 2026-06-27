"""ANSI codes, LOG_MODE choices, env vars, and global state for the logging system."""

from __future__ import annotations

import os
import time


def _ts() -> float:
    return time.time()


LOG_MODE_ENV = os.getenv("LOG_MODE", os.getenv("LOG_LEVEL", "DEBUG")).upper().strip()
LOG_MODE_CHOICES = {"DEBUG", "MEDIUM", "BASIC", "BASIC_PLAIN"}
LOG_MODE = LOG_MODE_ENV if LOG_MODE_ENV in LOG_MODE_CHOICES else "DEBUG"

LOG_SNIP_CHARS = int(os.getenv("LOG_SNIP_CHARS", "4000"))
BASIC_SNIP_CHARS = int(os.getenv("BASIC_SNIP_CHARS", "0"))
LOG_STREAM_CHUNKS = os.getenv("LOG_STREAM_CHUNKS", "0").strip().lower() in {"1", "true", "yes", "on"}
LOG_PLAIN_COLORS = os.getenv("LOG_PLAIN_COLORS", "1").strip().lower() in {"1", "true", "yes", "on"}
LOG_PLAIN_WRAP_WIDTH = int(os.getenv("LOG_PLAIN_WRAP_WIDTH", "80"))

# Plain-text request state (shared across all callers)
_PLAIN_LAST_REQ_ID: str | None = None
_PLAIN_CLOSED_REQ_IDS: set[str] = set()


# ── ANSI color codes ────────────────────────────────────────────────

ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"
ANSI_CYAN = "\x1b[36m"
ANSI_GREEN = "\x1b[32m"
ANSI_MAGENTA = "\x1b[35m"
ANSI_YELLOW = "\x1b[33m"
ANSI_BLUE = "\x1b[34m"
ANSI_RED = "\x1b[31m"
ANSI_GRAY = "\x1b[90m"


def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI color codes (no-op when colors disabled)."""
    if not LOG_PLAIN_COLORS or not codes:
        return text
    return "".join(codes) + text + ANSI_RESET
