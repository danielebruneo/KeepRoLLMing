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
import sys

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
    parser.add_argument("--tail", action="store_true", help="Tail the BASIC_PLAIN log after startup")
    parser.add_argument("--plain-log", dest="plain_log", action="store_true",
                        help="Also save BASIC_PLAIN output to keeprollming.log (reads from NDJSON)")
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

    # ── V2: Optional BASIC_PLAIN log generation ────────────────────
    if args.plain_log or args.tail:
        log_dir = os.environ.get("LOG_PATH", ".")
        json_log = os.path.join(log_dir, "keeprollming.log.json")
        plain_log = os.path.join(log_dir, "keeprollming.log")
        viewer_cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "scripts", "log-viewer.py"),
                      "--file", json_log]
        if args.plain_log:
            viewer_cmd.extend(["--plain-output", plain_log])
        if args.tail:
            import subprocess
            subprocess.Popen(viewer_cmd + (["--tail"] if args.tail else []),
                           stdout=sys.stderr if args.tail else subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            print(f"[arch_v2] JSON log: {json_log}")
            if args.plain_log:
                print(f"[arch_v2] PLAIN log: {plain_log}")

    uvicorn.run("keeprollming.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
