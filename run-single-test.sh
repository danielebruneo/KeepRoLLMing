#!/bin/bash

# Script to run a single test in a clean environment for reliable execution
# Usage: ./run-single-test.sh test_name

if [ $# -eq 0 ]; then
    echo "Usage: $0 <test_name>"
    echo "Example: $0 test_streaming_response_reconstruction"
    exit 1
fi

TEST_NAME=$1

echo "Running single test '$TEST_NAME' in clean environment..."

# Create virtual environment if it doesn't exist
if [ ! -d "test_env" ]; then
    python -m venv test_env
fi

# Activate virtual environment
source test_env/bin/activate

# Install requirements
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run specific test with no parallel execution to avoid issues
python -m pytest tests/test_orchestrator.py -k "$TEST_NAME" --tb=short -v

echo "Test run completed"