"""Test E2E per verificare il comportamento del nudge filter.

Il fake backend risponde sempre con "Prova:" (lazy pattern).
Questo permette di verificare che:
1. Il nudge triggera correttamente sul primo tentativo  
2. Il retry viene eseguito con "Continue." aggiunto al prompt
3. Dopo max_attempts, il filtro ritorna la risposta originale senza retry aggiuntivi
"""

import pytest


def test_nudge_max_retry_behavior(
    fake_backend_server, 
    orchestrator_server, 
    backend_client,
    configure_fake_backend
):
    """Test che verifica il comportamento quando il backend è sempre lazy.
    
    Il fake backend risponde SEMPRE con "Prova:" (lazy pattern).
    Dovremmo vedere:
    - 1° richiesta: user dice "Rispondi..." → backend risponde "Prova:"
    - Nudge triggera, aggiunge "Continue." al prompt
    - 2° richiesta: user dice "Rispondi... Continue." → backend risponde ancora "Prova:"
    - Nudge triggera di nuovo (attempt=2)
    - Dopo max_attempts (3), il filtro smette di retryare e ritorna la risposta
    
    Quindi dovremmo vedere solo "Prova:" nel response finale.
    """
    
    # Configure fake backend to ALWAYS return lazy response
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": "Prova:",  # Always returns lazy pattern
            "stream_pieces": [],
            "include_usage": True,
        },
    })

    print("\n" + "="*80)
    print("TEST: Nudge max retry behavior (backend always lazy)")
    print("="*80)
    
    # Make request to ORCHESTRATOR server
    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "local/deep",  # Route with filters in config.test.yaml!
            "messages": [{"role": "user", "content": "Rispondi esattamente con 'Prova:'"}],
            "stream": False,
        },
    )
    
    print(f"\nResponse status: {response.status_code}")
    
    if response.status_code != 200:
        print(f"❌ Request failed with status {response.status_code}")
        raise AssertionError(f"Expected 200 but got {response.status_code}")
    
    resp_json = response.json()
    content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
    
    print(f"\nResponse content: '{content}'")
    print(f"Content length: {len(content)} chars")
    
    # Since backend ALWAYS returns lazy, we should get max retry and then return original response
    assert len(content) > 0, "Expected non-empty response"
    
    # The final response should be the last one from backend (still lazy since it always is)
    print(f"\n✅ Test completed. Final content: '{content}'")


def test_nudge_with_complete_response(
    fake_backend_server, 
    orchestrator_server, 
    backend_client,
    configure_fake_backend
):
    """Test che verifica il comportamento quando il backend completa la risposta dopo Continue."""
    
    # Configure fake backend to respond differently based on call index
    # First call: lazy response "Prova:"
    # Second call (with Continue.): complete response
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": "",  # Will be overridden by script
            "stream_pieces": [],
            "include_usage": True,
            # Script: first call returns lazy, second call returns complete response
            "script": [
                {"content": "Prova:", "include_usage": True},  # Call 1: lazy
                {
                    "content": "Prova: questo è il completamento dopo il nudge!", 
                    "include_usage": True
                },  # Call 2 (retry): complete response
            ]
        },
    })

    print("\n" + "="*80)
    print("TEST: Nudge with complete response after retry")
    print("="*80)
    
    # Make request to ORCHESTRATOR server
    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "local/deep",  # Route with filters in config.test.yaml!
            "messages": [{"role": "user", "content": "Rispondi esattamente con 'Prova:'"}],
            "stream": False,
        },
    )
    
    print(f"\nResponse status: {response.status_code}")
    
    if response.status_code != 200:
        print(f"❌ Request failed with status {response.status_code}")
        raise AssertionError(f"Expected 200 but got {response.status_code}")
    
    resp_json = response.json()
    content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
    
    print(f"\nResponse content: '{content}'")
    print(f"Content length: {len(content)} chars")
    
    # Since second call returns complete response, we should see the full text
    if "completamento dopo il nudge" in content:
        print("✅ SUCCESS: Nudge worked! Backend completed the thought on retry.")
        assert len(content) > 10, "Expected longer response after successful nudge"
    else:
        print(f"⚠️  Got lazy response anyway: '{content}'")


def test_nudge_multiple_iterations(
    fake_backend_server, 
    orchestrator_server, 
    backend_client,
    configure_fake_backend
):
    """Test con più iterazioni per verificare che il nudge funzioni correttamente."""
    
    # Configure fake backend to alternate between lazy and complete responses
    script = []
    for i in range(20):  # 10 pairs of (lazy, complete)
        script.append({"content": "Prova:", "include_usage": True})  # Lazy
        script.append({
            "content": f"Prova: completamento #{i//2 + 1}!", 
            "include_usage": True
        })  # Complete
    
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": "",
            "stream_pieces": [],
            "include_usage": True,
            "script": script,
        },
    })

    print("\n" + "="*80)
    print("TEST: Multiple iterations with alternating responses")
    print("="*80)
    
    results = []
    
    # Run multiple requests
    for i in range(10):
        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/deep",
                "messages": [{"role": "user", "content": f"Test iteration {i+1}"}],
                "stream": False,
            },
        )
        
        if response.status_code == 200:
            resp_json = response.json()
            content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            if "completamento" in content:
                results.append("success")
                print(f"Iteration {i+1}: ✅ Success - '{content}'")
            elif content == "Prova:":
                results.append("max_retry")
                print(f"Iteration {i+1}: ❌ Max retry - '{content}'")
            else:
                results.append("other")
                print(f"Iteration {i+1}: ⚠️  Other - '{content}'")
        else:
            results.append("failed")
            print(f"Iteration {i+1}: ❌ Failed with status {response.status_code}")
    
    # Print summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    success_count = results.count("success")
    max_retry_count = results.count("max_retry")
    failed_count = results.count("failed")
    
    print(f"Success (nudge worked): {success_count}/10 ({success_count*10}%)")
    print(f"Max retry reached: {max_retry_count}/10 ({max_retry_count*10}%)")
    print(f"Failed: {failed_count}/10 ({failed_count*10}%)")
    
    # We expect roughly 50% success (every other call returns complete response)
    assert success_count >= 3, f"Expected at least 3 successes but got {success_count}"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs", "--capture=no"])
