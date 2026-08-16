#!/bin/bash

# Script to prepare the project-local development environment.

echo "Setting up test environment..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$SCRIPT_DIR/setup.sh" --dev
else
    source "$VENV_DIR/bin/activate"
    python -m pip install -e "$PROJECT_DIR[dev]"
fi

source "$VENV_DIR/bin/activate"

echo "Test environment ready"
