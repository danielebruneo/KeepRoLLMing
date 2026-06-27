#!/bin/bash
# scripts/test-fake-live.sh
# Live e2e: SystemPrompt, Nudge, TLS — both streaming and non-streaming.
# Usage: bash scripts/test-fake-live.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FAKE_PORT=19997
ORCH_PORT=18080
FAKE_URL="http://127.0.0.1:$FAKE_PORT"
ORCH_URL="http://127.0.0.1:$ORCH_PORT"
PASS=0; FAIL=0

assert_contains() { local d="$1" r="$2" e="$3"; if echo "$r" | grep -q "$e"; then echo "  ✅ $d"; PASS=$((PASS+1)); else echo "  ❌ $d (missing '$e')"; FAIL=$((FAIL+1)); fi; }
cleanup() { kill $FAKE_PID $ORCH_PID 2>/dev/null; wait; rm -f "$FAKE_CONFIG"; }
trap cleanup EXIT

echo "============================================"
echo " Live E2E: All 3 Filters (streaming + non-streaming)"
echo "============================================"

# Start fake backend
echo "--- Starting fake backend ---"
cd "$PROJECT_DIR"
python3 "$SCRIPT_DIR/start-fake-backend.py" --port "$FAKE_PORT" &
FAKE_PID=$!
sleep 2
curl -s "$FAKE_URL/health" > /dev/null || { echo "Fake backend failed"; exit 1; }

# Generate config
FAKE_CONFIG="/tmp/e2e_test_config_$$.yaml"
cat > "$FAKE_CONFIG" << EOF
models:
  fake-model:
    context_length: 131072
routes:
  base/fake:
    model: fake-model
    upstream_url: "$FAKE_URL"
  internal/fake:
    extends: base/fake
    pattern: "internal/fake"
    filter_chain:
      order: [system_prompt, model_nudge, model_tool_loop_stopper]
      filters:
        system_prompt:
          enabled: true
          prompt: "/nothink SYSTEM_TEST"
          override: false
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
          nudge_message: "Continue."
          max_nudge_attempts: 1
          upstream_url: "$FAKE_URL"
        model_tool_loop_stopper:
          enabled: true
          max_attempts: 1
          upstream_url: "$FAKE_URL"
EOF

# Start orchestrator
echo "--- Starting orchestrator ---"
export CONFIG_FILE="$FAKE_CONFIG"
export LOG_MODE=BASIC_PLAIN
cd "$PROJECT_DIR"
python3 -u keeprollming.py --port "$ORCH_PORT" &
ORCH_PID=$!
sleep 5
curl -s "$ORCH_URL/health" > /dev/null || { echo "Orchestrator failed"; exit 1; }

# Test 1: SystemPrompt (non-streaming)
echo ""; echo "=== Test 1: SystemPrompt (non-streaming) ==="
curl -s -X POST "$FAKE_URL/__scenario" -H "Content-Type: application/json" -d '{"chat_content":"FAKE OK"}' > /dev/null
RESP=$(curl -s -X POST "$ORCH_URL/v1/chat/completions" -H "Content-Type: application/json" -d '{"model":"internal/fake","messages":[{"role":"user","content":"Hello"}],"stream":false}')
assert_contains "Orchestrator responds" "$RESP" "FAKE OK"
CALLS=$(curl -s "$FAKE_URL/__calls")
MSGS=$(echo "$CALLS" | python3 -c "import sys,json; msgs=json.load(sys.stdin)['calls'][0]['payload']['messages']; print(json.dumps(msgs))")
assert_contains "System prompt injected" "$MSGS" "SYSTEM_TEST"

# Test 2: Nudge (non-streaming)
echo ""; echo "=== Test 2: Nudge (non-streaming) ==="
curl -s -X POST "$FAKE_URL/__scenario" -H "Content-Type: application/json" -d '{"chat_content":"I will say:"}' > /dev/null
curl -s -X POST "$ORCH_URL/v1/chat/completions" -H "Content-Type: application/json" -d '{"model":"internal/fake","messages":[{"role":"user","content":"Say something"}],"stream":false}' > /dev/null
CALLS=$(curl -s "$FAKE_URL/__calls")
CALL_COUNT=$(echo "$CALLS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['calls']))")
if [ "$CALL_COUNT" -ge 2 ]; then echo "  ✅ Nudge triggered retry (calls=$CALL_COUNT)"; PASS=$((PASS+1)); else echo "  ❌ Nudge did not trigger (calls=$CALL_COUNT)"; FAIL=$((FAIL+1)); fi

# Test 3: TLS (non-streaming)
echo ""; echo "=== Test 3: TLS (non-streaming) ==="
curl -s -X POST "$FAKE_URL/__scenario" -H "Content-Type: application/json" -d '{"chat_content":"Normal response"}' > /dev/null
RESP=$(curl -s -X POST "$ORCH_URL/v1/chat/completions" -H "Content-Type: application/json" -d '{"model":"internal/fake","messages":[{"role":"user","content":"Hello"}],"stream":false}')
assert_contains "TLS pass-through" "$RESP" "Normal response"

# Test 4: Nudge (streaming)
echo ""; echo "=== Test 4: Nudge (streaming) ==="
curl -s -X POST "$FAKE_URL/__scenario" -H "Content-Type: application/json" -d '{"chat_content":"I will say:"}' > /dev/null
curl -s -N -X POST "$ORCH_URL/v1/chat/completions" -H "Content-Type: application/json" -d '{"model":"internal/fake","messages":[{"role":"user","content":"Say something"}],"stream":true}' > /dev/null
CALLS=$(curl -s "$FAKE_URL/__calls")
CALL_COUNT=$(echo "$CALLS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['calls']))")
if [ "$CALL_COUNT" -ge 2 ]; then echo "  ✅ Nudge streaming triggered retry (calls=$CALL_COUNT)"; PASS=$((PASS+1)); else echo "  ❌ Nudge streaming did not trigger (calls=$CALL_COUNT)"; FAIL=$((FAIL+1)); fi

# Test 5: TLS (streaming)
echo ""; echo "=== Test 5: TLS (streaming) ==="
curl -s -X POST "$FAKE_URL/__scenario" -H "Content-Type: application/json" -d '{"chat_content":"Normal streaming"}' > /dev/null
RESP=$(curl -s -N -X POST "$ORCH_URL/v1/chat/completions" -H "Content-Type: application/json" -d '{"model":"internal/fake","messages":[{"role":"user","content":"Hello"}],"stream":true}')
assert_contains "TLS streaming pass-through" "$RESP" "data:"

echo ""; echo "============================================"
echo " Results: $PASS passed, $FAIL failed"
echo "============================================"
exit $FAIL
