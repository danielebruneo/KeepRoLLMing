#!/bin/bash
# scripts/setup.sh — Create virtual environment and install dependencies
# Usage: bash scripts/setup.sh
#
# Creates a .venv virtual environment in the project root and installs
# all dependencies from requirements.txt into it.
# After running, activate the venv:
#   source .venv/bin/activate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Check if venv already exists
if [[ -d "$PROJECT_DIR/.venv" ]]; then
    echo "Virtual environment already exists at $PROJECT_DIR/.venv"
    echo "Activate it with: source .venv/bin/activate"
    exit 0
fi

echo "Creating virtual environment at $PROJECT_DIR/.venv ..."
python3 -m venv "$PROJECT_DIR/.venv"

echo "Activating venv and installing dependencies ..."
source "$PROJECT_DIR/.venv/bin/activate"
pip install -r "$SCRIPT_DIR/../requirements.txt"

echo ""
echo "Setup complete. Activate the venv with:"
echo "  source .venv/bin/activate"
