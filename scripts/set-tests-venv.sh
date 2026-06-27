#!/bin/bash

# Script to set up and validate test virtual environment
# This script handles all venv creation logic to ensure clean test environments

echo "Setting up test environment..."

# Create virtual environment if it doesn't exist or is empty
if [ ! -d "../.test_venv" ]; then
    echo ".test_venv does not exist, creating..."
    mkdir ../.test_venv
    python -m venv ../.test_venv || {
        echo "Failed to create virtual environment"
        exit 1
    }
    echo ".test_venv created successfully"
elif [ -z "$(ls -A ../.test_venv)" ]; then
    # Directory exists but is empty
    echo ".test_venv exists but is empty, creating..."
    python -m venv ../.test_venv || {
        echo "Failed to create virtual environment"
        exit 1
    }
    echo ".test_venv created successfully"
else
    echo ".test_venv already exists and is not empty"
fi

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate virtual environment
source "$SCRIPT_DIR/../.test_venv/bin/activate"

echo "Test environment ready"