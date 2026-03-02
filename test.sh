curl -i -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"ciao, test"}],
    "max_tokens":120
  }' | head -n 40
