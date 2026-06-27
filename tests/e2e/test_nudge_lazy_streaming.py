"""E2E test: nudge filter su risposte lazy con pattern di produzione (code/red)."""

import json
import pytest


# Pattern esatti dalla route code/red di produzione
PROD_NUDGE_PATTERNS = [
    ":$",
    r'(?<![.!?])(?:^|\.\s+|\n)\s*Now\b[\s\S]*\.$',
    r'(?<![.!?])(?:^|\.\s+|\n)\s*Let\b[\s\S]*\.$',
    r'(?<![.!?])(?:^|\.\s+|\n)\s*Ora\b[\s\S]*\.$',
    r'(?<![.!?])(?:^|\.\s+|\n)\s*Devo\b[\s\S]*\.$',
    r'(?<![.!?])(?:^|\.\s+|\n)\s*Basta\b.*\.$',
    r'(?<![.!?])(?:^|\.\s+|\n)\s*((?:\S+\s+){0,4}\S+)\.$',
]


def test_nudge_lazy_streaming_let_pattern(
    fake_backend_server,
    orchestrator_server,
    backend_client,
    configure_fake_backend
):
    """Test nudge su risposta lazy 'Let me...' in streaming con pattern di produzione."""
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": "Let me check what format LibreChat expects for `allowedDomains`.",
            "stream_pieces": [
                "Let me check what format LibreChat expects for `allowedDomains`.\n",
                "The format should be a CIDR range like '172.18.0.0/16' with quotes."
            ],
            "include_usage": True,
        },
    })

    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "code/red",
            "messages": [
                {"role": "user", "content": "Che formato aspetta LibreChat per allowedDomains?"}
            ],
            "stream": True,
        },
    )

    assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

    chunks = []
    for line in response.iter_lines():
        if line and "data: [DONE]" not in line:
            try:
                data = json.loads(line.replace("data: ", ""))
                delta_content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta_content:
                    chunks.append(delta_content)
            except Exception:
                pass

    full_content = "".join(chunks)
    print(f"\nStreaming content: '{full_content}'")

    assert "The format should be a CIDR range" in full_content, \
        f"Nudge non ha triggerato! Content: '{full_content}'"
    print("✓ Streaming test PASSED!")


def test_nudge_lazy_streaming_now_pattern(
    fake_backend_server,
    orchestrator_server,
    backend_client,
    configure_fake_backend
):
    """Test nudge su risposta lazy 'Now...' in streaming."""
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": "Now restart the LibreChat container to pick up the new config.",
            "stream_pieces": [
                "Now restart the LibreChat container to pick up the new config.\n",
                "The configuration has been applied and LibreChat should now use the MCP settings."
            ],
            "include_usage": True,
        },
    })

    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "code/red",
            "messages": [{"role": "user", "content": "Cosa devo fare dopo?"}],
            "stream": True,
        },
    )

    assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

    chunks = []
    for line in response.iter_lines():
        if line and "data: [DONE]" not in line:
            try:
                data = json.loads(line.replace("data: ", ""))
                delta_content = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta_content:
                    chunks.append(delta_content)
            except Exception:
                pass

    full_content = "".join(chunks)
    print(f"\nStreaming content: '{full_content}'")

    assert "The configuration has been applied" in full_content, \
        f"Nudge non ha triggerato! Content: '{full_content}'"
    print("✓ Streaming 'Now...' test PASSED!")


def test_nudge_lazy_non_streaming_let_pattern(
    fake_backend_server,
    orchestrator_server,
    backend_client,
    configure_fake_backend
):
    """Test nudge su risposta lazy 'Let me...' in non-streaming con pattern di produzione."""
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": [
                "Let me check what format LibreChat expects for `allowedDomains`.",
                "The format should be a CIDR range like '172.18.0.0/16' with quotes."
            ],
            "stream_pieces": [],
            "include_usage": True,
        },
    })

    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "code/red",
            "messages": [
                {"role": "user", "content": "Che formato aspetta LibreChat per allowedDomains?"}
            ],
            "stream": False,
        },
    )

    assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

    resp_json = response.json()
    content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"\nNon-streaming content: '{content}'")

    assert "Let me check" in content, f"Missing lazy response in: '{content}'"
    assert "CIDR range" in content, f"Nudge non ha triggerato! Content: '{content}'"
    print("✓ Non-streaming test PASSED!")


def test_nudge_lazy_non_streaming_now_pattern(
    fake_backend_server,
    orchestrator_server,
    backend_client,
    configure_fake_backend
):
    """Test nudge su risposta lazy 'Now...' in non-streaming."""
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": [
                "Now restart the LibreChat container to pick up the new config.",
                "The configuration has been applied and LibreChat should now use the MCP settings."
            ],
            "stream_pieces": [],
            "include_usage": True,
        },
    })

    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "code/red",
            "messages": [{"role": "user", "content": "Cosa devo fare dopo?"}],
            "stream": False,
        },
    )

    assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

    resp_json = response.json()
    content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"\nNon-streaming content: '{content}'")

    assert "Now restart" in content, f"Missing lazy response in: '{content}'"
    assert "MCP settings" in content, f"Nudge non ha triggerato! Content: '{content}'"
    print("✓ Non-streaming 'Now...' test PASSED!")


def test_nudge_lazy_with_newline_in_content(
    fake_backend_server,
    orchestrator_server,
    backend_client,
    configure_fake_backend
):
    """Test nudge su risposta che contiene newline interno (simulando word wrap del modello)."""
    configure_fake_backend({
        "models": {
            "test-deep-model": {"context_length": 131072},
        },
        "chat": {
            "content": [
                "The domain check is comparing\nagainst the full URL but I put the CIDR.\nLet me check what format LibreChat expects for `allowedDomains`.",
                "The format should be a CIDR range like '172.18.0.0/16' with quotes."
            ],
            "stream_pieces": [],
            "include_usage": True,
        },
    })

    response = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "code/red",
            "messages": [
                {"role": "user", "content": "Che formato aspetta LibreChat per allowedDomains?"}
            ],
            "stream": False,
        },
    )

    assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

    resp_json = response.json()
    content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"\nNon-streaming content with newlines: '{content}'")

    assert "Let me check" in content, f"Missing lazy response in: '{content}'"
    assert "CIDR range" in content, f"Nudge non ha triggerato su testo con newline! Content: '{content}'"
    print("✓ Non-streaming with newlines test PASSED!")


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
