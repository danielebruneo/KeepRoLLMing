#!/bin/bash
# scripts/start-with-fake.sh
# Start fake backend + orchestrator with internal/fake route pointing to fake backend.
# Usage: bash scripts/start-with-fake.sh [--port 8000] [--fake-port 19997]

set -euo pipefail

ORCH_PORT=8000
FAKE_PORT=19997
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) ORCH_PORT="$2"; shift 2 ;;
        --fake-port) FAKE_PORT="$2"; shift 2 ;;
        *) echo "Usage: $0 [--port 8000] [--fake-port 19997]" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
if [[ -n "${KRM_PYTHON:-}" ]]; then
    PYTHON_BIN="$KRM_PYTHON"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi

FAKE_PID=""
ORCH_PID=""
FAKE_CONFIG=""
cleanup() {
    [[ -n "$ORCH_PID" ]] && kill "$ORCH_PID" 2>/dev/null || true
    [[ -n "$FAKE_PID" ]] && kill "$FAKE_PID" 2>/dev/null || true
    [[ -n "$FAKE_CONFIG" ]] && rm -f "$FAKE_CONFIG"
}
trap cleanup EXIT INT TERM

echo "=== Starting fake backend on port $FAKE_PORT ==="
"$PYTHON_BIN" "$SCRIPT_DIR/start-fake-backend.py" --port "$FAKE_PORT" &
FAKE_PID=$!
sleep 2

# Verify fake backend
if ! curl -s "http://127.0.0.1:$FAKE_PORT/health" > /dev/null 2>&1; then
    echo "ERROR: Fake backend did not start"
    exit 1
fi
echo "Fake backend PID: $FAKE_PID"

# Set a default scenario
curl -s -X POST "http://127.0.0.1:$FAKE_PORT/__scenario" \
    -H "Content-Type: application/json" \
    -d '{"chat_content": "FAKE OK"}' > /dev/null

# Generate temporary config pointing to fake backend
FAKE_CONFIG="/tmp/keeprollming_fake_config_$$.yaml"
cat > "$FAKE_CONFIG" << EOF
routes:
  base/fake:
    is_private: true
    model: fake-model
    upstream_url: "http://127.0.0.1:$FAKE_PORT"

  internal/fake:
    extends: base/fake
    pattern: "internal/fake"
    filters:
      system_prompt:
        enabled: true
        prompt: "Reply in French."
        override: false
      model_nudge:
        enabled: true
        trigger_patterns: [":$"]
        nudge_message: "Continue."
        max_attempts: 1
      model_tool_loop_stopper:
        enabled: true
        max_attempts: 1
EOF

echo "=== Starting orchestrator on port $ORCH_PORT ==="
echo "Config: $FAKE_CONFIG"
echo "Route: internal/fake -> http://127.0.0.1:$FAKE_PORT"
echo ""

cd "$PROJECT_DIR"
export CONFIG_FILE="$FAKE_CONFIG"

"$PYTHON_BIN" -u keeprollming.py --port "$ORCH_PORT" &
ORCH_PID=$!
sleep 4

if ! curl -s "http://127.0.0.1:$ORCH_PORT/health" > /dev/null 2>&1; then
    echo "ERROR: Orchestrator did not start"
    exit 1
fi

echo "Orchestrator PID: $ORCH_PID"
echo ""
echo "=== READY ==="
echo "Fake backend: http://127.0.0.1:$FAKE_PORT"
echo "Orchestrator: http://127.0.0.1:$ORCH_PORT"
echo "Route:        internal/fake"
echo "Filters:      system_prompt + model_nudge + model_tool_loop_stopper"
echo ""
echo "Test: curl -X POST http://127.0.0.1:$ORCH_PORT/v1/chat/completions \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"model\":\"internal/fake\",\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":false}'"
echo ""
echo "Set scenario: curl -X POST http://127.0.0.1:$FAKE_PORT/__scenario \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{\"chat_content\":\"CUSTOM RESPONSE\"}'"
echo ""
echo "Stop: kill $ORCH_PID $FAKE_PID"
echo ""

wait $ORCH_PID
