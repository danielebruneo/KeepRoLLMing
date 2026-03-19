"""KeepRoLLMing - minimal OpenAI-compatible chat completions orchestrator

This is the refactored (multi-module) version.

Run:
  python keeprollming.py

It still exposes:
  POST /v1/chat/completions
"""
from __future__ import annotations

import argparse
import os

import uvicorn

from keeprollming.app import app  # noqa: F401  (import to expose for uvicorn)
from keeprollming.config import (
    DEFAULT_CTX_LEN,
    UPSTREAM_BASE_URL,
    CONFIG,
    USER_ROUTES,
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
        default_ctx=DEFAULT_CTX_LEN,
        routes=len(USER_ROUTES),
    )

    uvicorn.run("keeprollming.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
