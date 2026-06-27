"""Test per verificare il nudge funziona attraverso l'orchestrator."""

import httpx
import pytest


def test_nudge_through_orchestrator(
    fake_backend_server,
    orchestrator_server,
    backend_client,
    configure_fake_backend
):
    """Test nudge filter through REAL orchestrator (NOT bypassed)."""

    # DEBUG: This print should definitely appear in test output
    with open('/tmp/test_start.log', 'w') as f:
        f.write("TEST STARTED\n")

    print("=" * 80)
    print("STARTING TEST: test_nudge_through_orchestrator")
    print(f"orchestrator_server.base_url = {orchestrator_server.base_url}")
    print(f"fake_backend_server.base_url = {fake_backend_server.base_url}")
    print("=" * 80)

    # Configure fake backend to return lazy response first, then full text on retry
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            # First call returns "Prova:", second call (after nudge with Continue.) returns full text
            "content": ["Prova:", "Questa è la risposta completa dopo il retry con Continue."],
            "stream_pieces": [],
            "include_usage": True,
        },
    })

    # DEBUG: Log backend configuration
    print("\n=== FAKE BACKEND CONFIGURATION ===")
    with httpx.Client() as client:
        resp = client.get(f"{fake_backend_server.base_url}/v1/models")
        print(f"Models: {resp.json()}")
    print("================================\n")

   # Make request to ORCHESTRATOR server (NOT direct backend!)
    # This goes through: Client → Orchestrator (config.test.yaml) → Fake Backend
    print(f"\n=== REQUEST TO ORCHESTRATOR ===")
    print(f"Model: 'local/deep'")
    print(f"URL: {orchestrator_server.base_url}/v1/chat/completions")
    print("================================\n")
    
    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",  # ← ORCHESTRATOR, not backend!
        json={
            "model": "local/deep",  # Route with filter_chain in config.test.yaml!
            "messages": [{"role": "user", "content": "Rispondi esattamente con 'Prova:'"}],
            "stream": False,
        },
    )

    print(f"\n=== Response status: {response.status_code} ===")
    if response.status_code != 200:
        print(f"Response text: {response.text}")
        raise AssertionError(f"Expected 200 but got {response.status_code}: {response.text}")

    # Print raw response to debug JSON parsing issues
    raw_content = response.text
    print(f"\n=== Raw response content (first 500 chars) ===")
    print(raw_content[:500])
    print("=== End raw response ===\n")

    try:
        resp_json = response.json()
    except Exception as e:
        print(f"ERROR parsing JSON: {e}")
        raise AssertionError(f"Response is not valid JSON: {raw_content[:200]}")

    content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
    finish_reason = resp_json.get("choices", [{}])[0].get("finish_reason", "missing")
    
    print(f"\n=== PARSED RESPONSE ===")
    print(f"Content: '{content}'")
    print(f"Content length: {len(content)} chars")
    print(f"Finish reason: '{finish_reason}'")
    print(f"=== END PARSED RESPONSE ===\n")

    # Nudge accumulates: lazy_response + "\n" + retry_response
    assert "Prova:" in content, f"Missing lazy response in: '{content}'"
    assert "Questa è la risposta completa dopo il retry con Continue." in content, \
        f"Missing retry content in: '{content}'"

    print(f"\n✓ Test PASSED! Response after nudge retry: '{content}'")
    
    # Read server stdout log to verify BASIC_PLAIN logs show nudge retry
    import glob
    import os
    
    # Find the orchestrator stdout log file (created by test fixture)
    log_files = glob.glob(os.path.expanduser("~/.pytest_cache/tmp*/keeprollming_app.stdout.log"))
    if log_files:
        log_file = sorted(log_files)[-1]  # Get most recent
        with open(log_file, 'r') as f:
            log_content = f.read()
        
        print(f"\n=== Server BASIC_PLAIN Logs ===")
        print(log_content[-3000:])  # Print last 3000 chars
        print("=== End of logs ===\n")
        
        # Verify nudge retry was logged
        assert "filter_triggered_nudge" in log_content, \
            f"Nudge filter not triggered! Logs:\n{log_content[-1000:]}"
        assert "nudge_retry_attempt" in log_content, \
            f"Nudge retry not attempted! Logs:\n{log_content[-1000:]}"
        
        print("✓ BASIC_PLAIN logs confirm nudge retry behavior!")


def test_nudge_streaming_through_orchestrator(
    fake_backend_server,
    orchestrator_server,
    backend_client,
    configure_fake_backend
):
    """Test streaming nudge filter through REAL orchestrator."""

    # Configure fake backend to return lazy response ending with ":" in streaming mode
    # The nudge filter will detect this and trigger a retry
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            # First call returns lazy response ending with ":" to trigger nudge
            "content": "Prova:",
            # But in streaming mode, we return the FULL text (simulating what a real LLM would do on retry)
            "stream_pieces": ["Q", "u", "e", "s", "t", "a", " ", "\u00E8", " ", "l", "a", " ", "r", "i", "s", "p", "o", "s", "t", "a", " ", "c", "o", "m", "p", "l", "e", "t", "a", " ", "d", "o", "p", "o", " ", "i", "l", " ", "r", "e", "t", "r", "y", " ", "c", "o", "n", " ", "C", "o", "n", "t", "i", "n", "u", "e", "."],
            "include_usage": True,
        },
    })

    # Make STREAMING request to ORCHESTRATOR server
    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "local/deep",  # Route with filter_chain in config.test.yaml!
            "messages": [{"role": "user", "content": "Rispondi esattamente con 'Prova:'"}],
            "stream": True,
        },
    )

    print(f"\n=== Streaming Response status: {response.status_code} ===")
    if response.status_code != 200:
        print(f"Response text: {response.text}")
        raise AssertionError(f"Expected 200 but got {response.status_code}: {response.text}")

    # Collect all streamed content
    chunks = []
    for line in response.iter_lines():
        if line and "data: [DONE]" not in line:
            try:
                import json
                data = json.loads(line.replace("data: ", ""))
                delta_content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta_content:
                    chunks.append(delta_content)
            except Exception as e:
                print(f"Error parsing chunk: {e}")

    full_content = "".join(chunks)
    print(f"\nTotal streamed content: '{full_content}'")
    print(f"Length: {len(full_content)} chars")

    # If nudge worked, we should see the full response from retry (not just "Prova:")
    assert "Questa è la risposta completa dopo il retry con Continue." in full_content, \
        f"Expected full response after retry but got: '{full_content}'"

    print(f"\n✓ Streaming test PASSED! Response after nudge retry contains expected text.")


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
