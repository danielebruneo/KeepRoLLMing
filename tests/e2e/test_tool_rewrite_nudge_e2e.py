"""E2E tests: tool_rewrite filter in chain does not prevent nudge from triggering.

Verifies that Pipeline.from_route_config() with dict configs correctly
creates all filters and iterates through them without crashing on is_enabled.

Tests cover:
- tool_rewrite + nudge chain (streaming + non-streaming)
- Extends inheritance: child inherits parent's filter_chain
- Full production chain: tool_rewrite + tls + nudge
"""

import json

import pytest


# ── Shared helpers ──────────────────────────────────────────────────

def _parse_streaming_content(response) -> str:
    """Extract concatenated content from SSE streaming response."""
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
    return "".join(chunks)


def _configure_lazy_response(configure_fake_backend):
    """Configure fake backend to return a lazy 'Let me...' response.
    
    Uses a 'script' to return different responses for each call.
    First call: lazy response ending with '.'
    Subsequent calls: completion response
    """
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
            "script": [
                # First call: lazy response (triggers nudge)
                {"content": "Let me check what format LibreChat expects for `allowedDomains`."},
                # Second call (nudge retry 1): completion
                {"content": "The format should be a CIDR range like '172.18.0.0/16' with quotes."},
                # Third call (nudge retry 2): non-lazy continuation
                {"content": "The domain pattern supports IPv4 CIDR and simple hostnames."},
                # Fourth call (nudge retry 3): non-lazy
                {"content": "Make sure to enclose the value in quotes."},
            ],
        },
    })


# ── tool_rewrite + nudge chain ──────────────────────────────────────

class TestToolRewriteWithNudge:
    """Verify nudge triggers when tool_rewrite is in the same filter chain."""

    def test_streaming_nudge_triggers_with_tool_rewrite(
        self,
        fake_backend_server,
        orchestrator_server,
        backend_client,
        configure_fake_backend
    ):
        """Streaming: tool_rewrite + nudge chain — nudge should trigger on lazy response."""
        _configure_lazy_response(configure_fake_backend)

        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "chain/tr-nudge/test-model",
                "messages": [
                    {"role": "user", "content": "What format does LibreChat expect for allowedDomains?"}
                ],
                "stream": True,
            },
        )

        assert response.status_code == 200, f"Status {response.status_code}: {response.text}"
        content = _parse_streaming_content(response)
        print(f"\nStreaming content: '{content}'")

        assert "CIDR range" in content, \
            f"Nudge did NOT trigger with tool_rewrite in chain. Content: '{content}'"
        print("✓ Streaming with tool_rewrite PASSED!")

    def test_non_streaming_nudge_triggers_with_tool_rewrite(
        self,
        fake_backend_server,
        orchestrator_server,
        backend_client,
        configure_fake_backend
    ):
        """Non-streaming: tool_rewrite + nudge chain — nudge should trigger on lazy response."""
        _configure_lazy_response(configure_fake_backend)

        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "chain/tr-nudge/test-model",
                "messages": [
                    {"role": "user", "content": "What format does LibreChat expect for allowedDomains?"}
                ],
                "stream": False,
            },
        )

        assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

        resp_json = response.json()
        content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"\nNon-streaming content: '{content}'")

        assert "Let me check" in content, f"Missing lazy pattern in: '{content}'"
        assert "CIDR range" in content, \
            f"Nudge did NOT trigger with tool_rewrite in non-streaming chain. Content: '{content}'"
        print("✓ Non-streaming with tool_rewrite PASSED!")


# ── Extends inheritance ─────────────────────────────────────────────

class TestExtendsInheritance:
    """Verify child route inherits parent's filter_chain via extends."""

    def test_streaming_child_inherits_chain(
        self,
        fake_backend_server,
        orchestrator_server,
        backend_client,
        configure_fake_backend
    ):
        """Streaming: child route (extends parent) — nudge should trigger."""
        _configure_lazy_response(configure_fake_backend)

        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "chain/tr-nudge/inherited/test-model",
                "messages": [
                    {"role": "user", "content": "What format does LibreChat expect for allowedDomains?"}
                ],
                "stream": True,
            },
        )

        assert response.status_code == 200, f"Status {response.status_code}: {response.text}"
        content = _parse_streaming_content(response)
        print(f"\nStreaming content (inherited): '{content}'")

        assert "CIDR range" in content, \
            f"Nudge did NOT trigger on inherited chain. Content: '{content}'"
        print("✓ Streaming with extends inheritance PASSED!")

    def test_non_streaming_child_inherits_chain(
        self,
        fake_backend_server,
        orchestrator_server,
        backend_client,
        configure_fake_backend
    ):
        """Non-streaming: child route (extends parent) — nudge should trigger."""
        _configure_lazy_response(configure_fake_backend)

        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "chain/tr-nudge/inherited/test-model",
                "messages": [
                    {"role": "user", "content": "What format does LibreChat expect for allowedDomains?"}
                ],
                "stream": False,
            },
        )

        assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

        resp_json = response.json()
        content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"\nNon-streaming content (inherited): '{content}'")

        assert "Let me check" in content, f"Missing lazy pattern in: '{content}'"
        assert "CIDR range" in content, \
            f"Nudge did NOT trigger on inherited chain (non-streaming). Content: '{content}'"
        print("✓ Non-streaming with extends inheritance PASSED!")


# ── Full production chain: tool_rewrite + tls + nudge ───────────────

class TestFullProductionChain:
    """Verify nudge triggers with full production chain (tr + tls + nudge)."""

    def test_streaming_full_chain(
        self,
        fake_backend_server,
        orchestrator_server,
        backend_client,
        configure_fake_backend
    ):
        """Streaming: tool_rewrite + tls + nudge chain — nudge should trigger."""
        _configure_lazy_response(configure_fake_backend)

        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "chain/tr-tls-nudge/test-model",
                "messages": [
                    {"role": "user", "content": "What format does LibreChat expect for allowedDomains?"}
                ],
                "stream": True,
            },
        )

        assert response.status_code == 200, f"Status {response.status_code}: {response.text}"
        content = _parse_streaming_content(response)
        print(f"\nStreaming content (full chain): '{content}'")

        assert "CIDR range" in content, \
            f"Nudge did NOT trigger with full production chain. Content: '{content}'"
        print("✓ Streaming with full chain (tr + tls + nudge) PASSED!")

    def test_non_streaming_full_chain(
        self,
        fake_backend_server,
        orchestrator_server,
        backend_client,
        configure_fake_backend
    ):
        """Non-streaming: tool_rewrite + tls + nudge chain — nudge should trigger."""
        _configure_lazy_response(configure_fake_backend)

        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "chain/tr-tls-nudge/test-model",
                "messages": [
                    {"role": "user", "content": "What format does LibreChat expect for allowedDomains?"}
                ],
                "stream": False,
            },
        )

        assert response.status_code == 200, f"Status {response.status_code}: {response.text}"

        resp_json = response.json()
        content = resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"\nNon-streaming content (full chain): '{content}'")

        assert "Let me check" in content, f"Missing lazy pattern in: '{content}'"
        assert "CIDR range" in content, \
            f"Nudge did NOT trigger with full production chain (non-streaming). Content: '{content}'"
        print("✓ Non-streaming with full chain (tr + tls + nudge) PASSED!")


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
