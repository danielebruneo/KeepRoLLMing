#!/bin/bash

# Script to run tests in parallel mode using a clean environment
# Usage: ./run-parallel-tests.sh [optional_test_name]

echo "Setting up test environment for parallel execution..."

# Use dedicated venv setup script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/set-tests-venv.sh"

# Install requirements
pip install -r "$SCRIPT_DIR/../requirements.txt"
pip install -r "$SCRIPT_DIR/../requirements-dev.txt"

# Check if specific tests are provided as arguments
if [ $# -gt 0 ]; then
    # If specific tests are passed, run them normally
    echo "Running specific test(s)..."
    python -m pytest --tb=no -v "$@"
else
    # Run all tests: first parallelizable in parallel, then non-parallelizable in single-thread
    
    echo ""
    echo "=========================================="
    echo "PHASE 1: Running parallelizable tests (parallel mode)"
    echo "=========================================="
    
    # Phase 1: Run only parallelizable tests (excluding e2e and live tests)
    python -m pytest --tb=no -n auto -v \
        --ignore=tests/e2e/ \
        --ignore=live_tests/ \
        || PHASE1_EXIT=$?

    echo ""
    echo "=========================================="
    echo "PHASE 2: Running E2E tests (single-thread)"
    echo "=========================================="

    # Phase 2: Run E2E tests in single-thread mode (they use shared resources)
    python -m pytest --tb=no -n 0 -v \
        tests/e2e/ \
        || PHASE2_EXIT=$?
fi

echo ""
echo "Parallel test run completed"