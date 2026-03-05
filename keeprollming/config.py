from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ----------------------------
# Configuration
# ----------------------------

UPSTREAM_BASE_URL = os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")

# Backwards compatible defaults (when client passes an explicit model name, not an alias)
MAIN_MODEL = os.getenv("MAIN_MODEL", "qwen2.5-3b-instruct")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "qwen2.5-1.5b-instruct")

# Profile defaults (requested)
QUICK_MAIN_MODEL = os.getenv("QUICK_MAIN_MODEL", MAIN_MODEL)
QUICK_SUMMARY_MODEL = os.getenv("QUICK_SUMMARY_MODEL", SUMMARY_MODEL)

BASE_MAIN_MODEL = os.getenv("BASE_MAIN_MODEL", "qwen2.5-v1-7b-instruct")
BASE_SUMMARY_MODEL = os.getenv("BASE_SUMMARY_MODEL", "qwen2.5-3b-instruct")

DEEP_MAIN_MODEL = os.getenv("DEEP_MAIN_MODEL", "qwen2.5-27b-instruct")
DEEP_SUMMARY_MODEL = os.getenv("DEEP_SUMMARY_MODEL", "qwen2.5-7b-instruct")

DEFAULT_CTX_LEN = int(os.getenv("DEFAULT_CTX_LEN", "4096"))
SUMMARY_MAX_TOKENS = int(os.getenv("SUMMARY_MAX_TOKENS", "256"))
SAFETY_MARGIN_TOK = int(os.getenv("SAFETY_MARGIN_TOK", "128"))

# Max chars for logging large payloads (input conversation, summary requests, etc.)
LOG_PAYLOAD_MAX_CHARS = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", "20000000"))

PASSTHROUGH_PREFIX = "pass/"


@dataclass(frozen=True)
class Profile:
    name: str
    main_model: str
    summary_model: str


PROFILES: Dict[str, Profile] = {
    "quick": Profile("quick", QUICK_MAIN_MODEL, QUICK_SUMMARY_MODEL),
    "main":  Profile("main",  BASE_MAIN_MODEL,  BASE_SUMMARY_MODEL),
    "deep":  Profile("deep",  DEEP_MAIN_MODEL,  DEEP_SUMMARY_MODEL),
}

# Client-facing model aliases (LibreChat or your own)
MODEL_ALIASES: Dict[str, str] = {
    "local/quick": "quick",
    "quick": "quick",

    "local/main": "main",
    "main": "main",

    "local/deep": "deep",
    "deep": "deep",
}


def resolve_profile_and_models(client_model: str) -> Tuple[Optional[Profile], str, str, bool]:
    """Return (profile_or_none, upstream_main_model, summary_model, passthrough_enabled)."""
    if isinstance(client_model, str) and client_model.startswith(PASSTHROUGH_PREFIX):
        backend = client_model[len(PASSTHROUGH_PREFIX):].strip()
        # If empty, fallback to MAIN_MODEL but keep passthrough disabled to avoid surprises
        if not backend:
            return (None, MAIN_MODEL, SUMMARY_MODEL, False)
        return (None, backend, SUMMARY_MODEL, True)

    key = MODEL_ALIASES.get(client_model)
    if key and key in PROFILES:
        p = PROFILES[key]
        return (p, p.main_model, p.summary_model, False)

    # No alias: treat it as an explicit upstream model name (backwards-compatible)
    return (None, client_model or MAIN_MODEL, SUMMARY_MODEL, False)
