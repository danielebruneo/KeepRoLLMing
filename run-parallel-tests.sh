#!/bin/bash

# Script to run tests in parallel mode using a clean environment
# Usage: ./run-parallel-tests.sh [optional_test_name]

echo "Setting up test environment for parallel execution..."

# Create virtual environment if it doesn't exist
if [ ! -d "test_env" ]; then
    python -m venv test_env
fi

# Activate virtual environment
source test_env/bin/activate

# Install requirements
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests with parallel execution (using pytest-xdist)
if [ $# -eq 0 ]; then
    # Run all tests in parallel mode
    python -m pytest --tb=no -n auto -v
else
    # Run specific test(s) in parallel mode
    python -m pytest --tb=no -n auto -v "$@"
fi

echo "Parallel test run completed"