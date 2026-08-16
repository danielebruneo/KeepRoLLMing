"""Reproduce the production bug with nudge filter causing 500 error.

Production logs show:
- Route bot/heartbeat has filters configured as dict
- Request is NON-streaming (process_non_streaming_request)
- Error occurs immediately after "About to call filters.process_response"
- Response content appears to be just "..." (three dots)

This test reproduces the exact scenario.
"""

import pytest
from fastapi.testclient import TestClient

import keeprollming.app as app_mod


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create a test client with isolated runtime state."""
    yield TestClient(app_mod.app)


def test_nudge_filter_with_ellipsis_content(client):
    """Test nudge filter with response containing just '...' (production bug reproduction)."""
    import keeprollming.app as app_mod
    
    # Make NON-streaming request that will trigger lazy pattern detection
    messages = [
        {"role": "user", "content": "Rispondi esattamente con \"Ora faccio questo:\""}
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/quick",  # Uses quick route without filters
            "messages": messages,
            "stream": False,
            "max_tokens": 64,
        },
    )

    print(f"\n=== Response status: {response.status_code} ===")
    if response.status_code != 200:
        print(f"Response text: {response.text}")
    
    assert response.status_code == 200, f"Expected 200 but got {response.status_code}: {response.text}"


def test_streaming_with_filter_chain_dict(client):
    """Test streaming with a route that has filters as dict.
    
    This is the exact scenario from production:
    - Route has filters configured as dict (not None)
    - Streaming request
    - Should not cause RuntimeWarning or 500 error
    """
    import keeprollming.app as app_mod
    
    messages = [
        {"role": "user", "content": "Say something with a colon:"}
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/quick",  # Uses quick route (no filters)
            "messages": messages,
            "stream": True,
            "max_tokens": 64,
        },
    )

    print(f"\n=== Streaming Response status: {response.status_code} ===")
    if response.status_code != 200:
        print(f"Response text: {response.text}")
    
    assert response.status_code == 200, f"Expected 200 but got {response.status_code}: {response.text}"
    
    # Should have streamed some content
    chunks = []
    for line in response.iter_lines():
        if line:
            chunks.append(line)
    
    assert len(chunks) > 0, "Expected at least one chunk"
