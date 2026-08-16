#!/bin/bash

# Script to run selected tests in the project development environment.
# Usage: ./run-single-test.sh tests/path/to/test.py [pytest options]

if [ $# -eq 0 ]; then
    echo "Usage: $0 tests/path/to/test.py [pytest options]"
    echo "Example: $0 tests/streaming/test_runner.py -k recovery"
    exit 1
fi

echo "Running selected test(s) in the project development environment..."

# Use dedicated venv setup script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/set-tests-venv.sh"

# Run exactly the requested target(s) with no parallel execution.
python -m pytest --tb=short -v "$@"

echo "Test run completed"
