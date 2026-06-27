#!/usr/bin/env python3
"""Thin wrapper to start the canonical fake backend from tests.e2e.fake_backend.

This script provides a CLI entrypoint for manually starting the fake backend
during development/testing. The actual implementation lives in:
    tests/e2e/fake_backend.py

Usage:
    python3 scripts/start-fake-backend.py --port 19997

Endpoints (same as tests.e2e.fake_backend):
    POST /v1/chat/completions  - OpenAI-compatible (streaming + non-streaming)
    POST /__scenario           - Set response scenario
    POST /__degrade            - Set degradation level (L0-L4)
    GET  /__calls              - Get call history
    GET  /health               - Health check
"""
import argparse
import sys
from pathlib import Path

# Add project root to path so we can import tests.e2e.fake_backend
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def main():
    parser = argparse.ArgumentParser(
        description="Start the canonical fake backend for testing"
    )
    parser.add_argument("--port", type=int, default=19997, help="Server port")
    args = parser.parse_args()

    from tests.e2e.fake_backend import create_app
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


if __name__ == "__main__":
    main()
