#!/bin/bash
# Test script for nudge retry functionality
# Usage: ./test_nudge_retry.sh [streaming|non-streaming]

SERVER_URL="${1:-http://localhost:8000}"
STREAMING="${2:-non-streaming}"

case "$STREAMING" in
  streaming)
    echo "Testing streaming mode..."
    curl -s -X POST "$SERVER_URL/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -d '{
        "model": "local/deep",
        "messages": [{"role": "user", "content": "/no_think Write \"Step 1:\""}],
        "stream": true,
        "temperature": 0.1
      }' | while read line; do 
        echo "$line" | python3 -c "import sys,json; d=json.loads(sys.stdin.read().replace('data: ','')); print(d.get('choices',[{}])[0].get('delta',{}).get('content',''))"; 
      done
    ;;
  non-streaming|*)
    echo "Testing non-streaming mode..."
    curl -s -X POST "$SERVER_URL/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -d '{
        "model": "local/deep",
        "messages": [{"role": "user", "content": "/no_think Reply exactly with \"OK:\""}],
        "stream": false,
        "temperature": 0.1
      }' | python3 -m json.tool | grep '"content"' -A 2 | head -5
    ;;
esac

echo ""
echo "Response captured above. Check server logs for nudge retry events:"
echo "grep -E 'FILTER_TRIGGERED|NUDGE_RETRY|assistant' /tmp/server_stdout.log"
