"""KeepRoLLMing - minimal OpenAI-compatible chat completions orchestrator

This is the refactored (multi-module) version.

Run:
  python keeprollming_orchestrator_profiles.py

It still exposes:
  POST /v1/chat/completions
"""
from __future__ import annotations

import argparse
import os

import uvicorn

from keeprollming.app import app  # noqa: F401  (import to expose for uvicorn)
from keeprollming.config import (
    BASE_MAIN_MODEL,
    BASE_SUMMARY_MODEL,
    DEFAULT_CTX_LEN,
    DEEP_MAIN_MODEL,
    DEEP_SUMMARY_MODEL,
    MAIN_MODEL,
    QUICK_MAIN_MODEL,
    QUICK_SUMMARY_MODEL,
    SUMMARY_MODEL,
    UPSTREAM_BASE_URL,
)
from keeprollming.logger import LOG_MODE, LOG_MODE_CHOICES, log


def main() -> None:
    parser = argparse.ArgumentParser(description="KeepRoLLMing orchestrator")
    parser.add_argument("--log-mode", "--log-level", dest="log_mode", choices=sorted(LOG_MODE_CHOICES), help="Logging verbosity (overrides env LOG_MODE/LOG_LEVEL)")
    parser.add_argument("--host", dest="host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", dest="port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()

    if args.log_mode:
        # override module global
        import keeprollming.logger as _logger
        _logger.LOG_MODE = args.log_mode.upper()

    log(
        "INFO",
        "startup",
        upstream=UPSTREAM_BASE_URL,
        main_model=MAIN_MODEL,
        summary_model=SUMMARY_MODEL,
        log_mode=LOG_MODE,
        profiles={
            "quick": {"main": QUICK_MAIN_MODEL, "summary": QUICK_SUMMARY_MODEL},
            "main": {"main": BASE_MAIN_MODEL, "summary": BASE_SUMMARY_MODEL},
            "deep": {"main": DEEP_MAIN_MODEL, "summary": DEEP_SUMMARY_MODEL},
        },
        default_ctx=DEFAULT_CTX_LEN,
    )

    uvicorn.run("keeprollming.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
