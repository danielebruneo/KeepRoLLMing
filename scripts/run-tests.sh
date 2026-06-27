#!/bin/bash

# Script to run tests in a clean environment to avoid compatibility issues
echo "Setting up test environment..."

# Use dedicated venv setup script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/set-tests-venv.sh"

# Install requirements
pip install -r "$SCRIPT_DIR/../requirements.txt"
pip install -r "$SCRIPT_DIR/../requirements-dev.txt"

# Run tests with no parallel execution to avoid issues
python -m pytest --tb=no -n0 -v

echo "Test run completed"