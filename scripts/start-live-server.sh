#!/bin/bash
# scripts/start-live-server.sh
# Consolidated script to start the orchestrator server for BASIC_PLAIN debugging
# Usage: ./scripts/start-live-server.sh [--port 8000]

set -e

# Configuration - use absolute path
PORT="${PORT:-8000}"
BASE_LOG_DIR="/tmp"

# Activate virtual environment
source .venv/bin/activate

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            PORT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--port 8000]"
            exit 1
            ;;
    esac
done

# Get current timestamp for unique log file name
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${BASE_LOG_DIR}/server_${TIMESTAMP}.log"

echo "=== Starting orchestrator server ===" >&2
echo "Timestamp: ${TIMESTAMP}" >&2
echo "Log file: ${LOG_FILE}" >&2
echo "Port: ${PORT}" >&2
echo "" >&2

# Change to project directory
cd "$(dirname "$0")/.."

# Start server with proper configuration:
# - nohup: prevent termination when parent shell exits
# - LOG_MODE=BASIC_PLAIN: enable structured logging
# - python -u: unbuffered stdout for real-time logs
# - stdout + stderr go to main log; stderr also goes to separate err.log

# Export environment variable first
export LOG_MODE=BASIC_PLAIN

ERR_FILE="${LOG_FILE%.log}_err.log"

nohup bash -c '
if [[ -d .test_venv ]]; then source .test_venv/bin/activate; elif [[ -d .venv ]]; then source .venv/bin/activate; fi
python -u keeprollming.py --port '"${PORT}"' 2> >(tee -a '"${ERR_FILE}"' >> '"${LOG_FILE}"') > '"${LOG_FILE}"'' &

SERVER_PID=$!
echo "Server PID: ${SERVER_PID}" >&2
echo "" >&2

# Wait for server to initialize
sleep 2

# Health check
echo "Performing health check..." >&2
HEALTH_RESPONSE=$(curl -s http://localhost:${PORT}/health || echo "FAILED")

if echo "$HEALTH_RESPONSE" | grep -q '"status":"ok"'; then
    echo "" >&2
    echo "✓ Server started successfully!" >&2
    echo "  PID: ${SERVER_PID}" >&2
    echo "  Log file: ${LOG_FILE}" >&2
    echo "  Err log:  ${ERR_FILE}" >&2
    echo "" >&2
    echo "To monitor logs in real-time:" >&2
    echo "  tail -f ${LOG_FILE}" >&2
    echo "  tail -f ${ERR_FILE}" >&2
    echo "" >&2
    echo "To kill the server:" >&2
    echo "  kill ${SERVER_PID}" >&2
    echo "" >&2
    
    # Return success with PID and log file path
    echo "[PID] ${SERVER_PID}: [OK] ${LOG_FILE}"
else
    echo "" >&2
    echo "✗ Health check failed!" >&2
    echo "Response: ${HEALTH_RESPONSE}" >&2
    echo "" >&2
    
    # Show last 30 lines of log for debugging
    if [[ -f "${LOG_FILE}" ]]; then
        echo "Last 30 lines of log:" >&2
        tail -30 "${LOG_FILE}" >&2
    fi
    
    # Return failure
    echo "[ERROR] Server failed to start"
    exit 1
fi
