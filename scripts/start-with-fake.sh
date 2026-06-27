#!/bin/bash
# scripts/start-with-fake.sh
# Start fake backend + orchestrator with internal/fake route pointing to fake backend.
# Usage: bash scripts/start-with-fake.sh [--port 8000] [--fake-port 19997]

set -e

ORCH_PORT="${1:-8000}"
FAKE_PORT="${2:-19997}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Starting fake backend on port $FAKE_PORT ==="
python3 "$SCRIPT_DIR/start-fake-backend.py" --port "$FAKE_PORT" &
FAKE_PID=$!
sleep 2

# Verify fake backend
if ! curl -s "http://127.0.0.1:$FAKE_PORT/health" > /dev/null 2>&1; then
    echo "ERROR: Fake backend did not start"
    kill $FAKE_PID 2>/dev/null
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
models:
  fake-model:
    context_length: 131072

routes:
  base/fake:
    model: fake-model
    upstream_url: "http://127.0.0.1:$FAKE_PORT"

  internal/fake:
    extends: base/fake
    pattern: "internal/fake"
    filter_chain:
      order: [system_prompt, model_nudge, model_tool_loop_stopper]
      filters:
        system_prompt:
          enabled: true
          prompt: "/nothink reply in french"
          override: false
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
          nudge_message: "Continue."
          max_nudge_attempts: 1
          upstream_url: "http://127.0.0.1:$FAKE_PORT"
        model_tool_loop_stopper:
          enabled: true
          max_attempts: 1
          upstream_url: "http://127.0.0.1:$FAKE_PORT"
EOF

echo "=== Starting orchestrator on port $ORCH_PORT ==="
echo "Config: $FAKE_CONFIG"
echo "Route: internal/fake -> http://127.0.0.1:$FAKE_PORT"
echo ""

cd "$PROJECT_DIR"
export LOG_MODE=BASIC_PLAIN
export CONFIG_FILE="$FAKE_CONFIG"

python3 -u keeprollming.py --port "$ORCH_PORT" &
ORCH_PID=$!
sleep 4

if ! curl -s "http://127.0.0.1:$ORCH_PORT/health" > /dev/null 2>&1; then
    echo "ERROR: Orchestrator did not start"
    kill $ORCH_PID $FAKE_PID 2>/dev/null
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
kill $FAKE_PID 2>/dev/null
rm -f "$FAKE_CONFIG"
