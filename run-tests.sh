#!/bin/bash

# Script to run tests in a clean environment to avoid compatibility issues
echo "Setting up test environment..."

# Use dedicated venv setup script
source set-tests-venv.sh

# Install requirements
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests with no parallel execution to avoid issues
python -m pytest --tb=no -n0 -v

echo "Test run completed"