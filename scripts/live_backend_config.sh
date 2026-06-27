#!/bin/bash

# Configuration for running e2e_live tests against a local LM Studio backend
export E2E_LIVE_BASE_URL="http://arkai.local:1234"
export E2E_LIVE_MODEL="qwen3-coder-30b-a3b-instruct"

echo "Live backend configuration loaded:"
echo "E2E_LIVE_BASE_URL=$E2E_LIVE_BASE_URL"
echo "E2E_LIVE_MODEL=$E2E_LIVE_MODEL"