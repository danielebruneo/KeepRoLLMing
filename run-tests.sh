#!/bin/bash

# Script to run tests in a clean environment to avoid compatibility issues
echo "Setting up test environment..."

# Create virtual environment if it doesn't exist
if [ ! -d "test_env" ]; then
    python -m venv test_env
fi

# Activate virtual environment
source test_env/bin/activate

# Install requirements
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests with no parallel execution to avoid issues
python -m pytest --tb=no -n0 -v

echo "Test run completed"