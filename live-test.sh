#!/bin/bash

# Script for running live E2E tests against your LM Studio backend

# Import the configuration
source ./live_backend_config.sh

echo "=== Live Test Configuration ==="
echo "E2E_LIVE_BASE_URL=$E2E_LIVE_BASE_URL"
echo "E2E_LIVE_MODEL=$E2E_LIVE_MODEL"
echo "E2E_LIVE_CLIENT_SUMMARY_MODEL=$E2E_LIVE_CLIENT_SUMMARY_MODEL"
echo "==============================="

echo ""
echo "Running live E2E tests..."
pytest -m e2e_live --tb=short -v