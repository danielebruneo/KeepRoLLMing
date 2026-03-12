# Test 1: Basic Chat Completion
curl -i -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"ciao"}],
    "max_tokens":120
  }' | head -n 40

# Test 2: Streaming Response
curl -i -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"ciao"}],
    "stream":true
  }' | head -n 40

# Test 3: Passthrough Mode
curl -i -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"pass/my-backend-model",
    "messages":[{"role":"user","content":"ciao"}]
  }' | head -n 40

# Test 4: Long Prompt with Summary
curl -i -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"x"*4000}],
    "max_tokens":256
  }' | head -n 40

# Test 5: Multiple Messages
curl -i -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"ciao"},{"role":"assistant","content":"ok"}],
    "max_tokens":256
  }' | head -n 40