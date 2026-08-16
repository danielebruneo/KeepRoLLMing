#!/bin/bash
# scripts/setup.sh — Create virtual environment and install dependencies
# Usage: bash scripts/setup.sh [--dev]
#
# Creates a .venv virtual environment in the project root and installs the
# package. Pass --dev to include test and lint tooling.
# After running, activate the venv:
#   source .venv/bin/activate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_DEV=false
if [[ "${1:-}" == "--dev" ]]; then
    INSTALL_DEV=true
elif [[ $# -gt 0 ]]; then
    echo "Usage: $0 [--dev]" >&2
    exit 2
fi

PYTHON_BIN="${KRM_PYTHON:-python3}"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    echo "ERROR: KeepRoLLMing requires Python 3.11 or newer (set KRM_PYTHON to choose Python)." >&2
    exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    echo "Creating virtual environment at $PROJECT_DIR/.venv ..."
    "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"
elif [[ ! -f "$PROJECT_DIR/.venv/bin/activate" ]]; then
    echo "Repairing incomplete virtual environment at $PROJECT_DIR/.venv ..."
    "$PYTHON_BIN" -m venv --upgrade "$PROJECT_DIR/.venv"
fi

echo "Activating venv and installing KeepRoLLMing ..."
source "$PROJECT_DIR/.venv/bin/activate"
python -m pip install --upgrade pip
if "$INSTALL_DEV"; then
    python -m pip install -e "$PROJECT_DIR[dev]"
else
    python -m pip install -e "$PROJECT_DIR"
fi

echo ""
echo "Setup complete. Activate the venv with:"
echo "  source .venv/bin/activate"
if ! "$INSTALL_DEV"; then
    echo "For contributor tooling and tests, run: bash scripts/setup.sh --dev"
fi
