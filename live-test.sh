#!/bin/bash

# Script for running live E2E tests against your LM Studio backend

export E2E_LIVE_BASE_URL="http://arkai.local:1234"
export E2E_LIVE_MODEL="qwen3-coder-30b-a3b-instruct"
export E2E_LIVE_CLIENT_SUMMARY_MODEL="local/quick"

echo "=== Live Test Configuration ==="
echo "E2E_LIVE_BASE_URL=$E2E_LIVE_BASE_URL"
echo "E2E_LIVE_MODEL=$E2E_LIVE_MODEL"
echo "E2E_LIVE_CLIENT_SUMMARY_MODEL=$E2E_LIVE_CLIENT_SUMMARY_MODEL"
echo "==============================="

echo ""
echo "Running live E2E tests..."
pytest -m e2e_live --tb=short -v