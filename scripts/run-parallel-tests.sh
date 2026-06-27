#!/bin/bash
# Wrapper: runs the full test suite and captures output to a timestamped log file.
# Usage: ./scripts/run-parallel-tests.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGFILE="/tmp/test_suite_$(date +%Y%m%d_%H%M%S).log"

"$SCRIPT_DIR/_run_tests.sh" 2>&1 | tee "$LOGFILE"

echo ""
echo "Full log: $LOGFILE"
