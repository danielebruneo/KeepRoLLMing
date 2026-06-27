"""E2E tests for error handling scenarios.

Verifies upstream timeout, fallback chain, empty messages,
and concurrent requests.
"""

import asyncio
import httpx
import pytest


@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestErrorHandlingE2E:
    """Error handling scenarios through the orchestrator."""

    def test_upstream_timeout_nonstreaming(self, orchestrator_server, backend_target, backend_client):
        """Upstream delayed beyond timeout → orchestrator returns 504."""
        # backend_target.control_url points to the fake backend (not the orchestrator)
        control_url = backend_target.control_url
        assert control_url, "control_url must be set for fake mode"

        # Tell fake backend to delay the next request by 3s
        httpx.post(f"{control_url}/__control", json={"action": "delay", "ms": 3000}, timeout=5.0)

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/quick",
                "messages": [{"role": "user", "content": "test timeout"}],
                "stream": False,
            },
        )
        # Timeout should result in a 504 or 500 with error message
        assert resp.status_code in (504, 500, 502), f"Expected error status, got {resp.status_code}: {resp.text[:200]}"
        body = resp.json()
        assert "error" in body, f"No error in response: {body}"

    def test_upstream_timeout_streaming(self, orchestrator_server, backend_target, backend_client):
        """Upstream delayed beyond timeout → orchestrator returns error in streaming."""
        control_url = backend_target.control_url
        assert control_url, "control_url must be set for fake mode"

        httpx.post(f"{control_url}/__control", json={"action": "delay", "ms": 3000}, timeout=5.0)

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/quick",
                "messages": [{"role": "user", "content": "test timeout streaming"}],
                "stream": True,
            },
        )
        # Streaming with timeout should return SSE with error event or [DONE]
        assert resp.status_code in (200, 504, 500), f"Got {resp.status_code}"

    def test_upstream_500_non_summary(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Upstream returns 500 on normal request → error propagated to client."""
        configure_fake_backend({
            "chat": {
                "script": [{"type": "error", "status": 500, "message": "temporary backend failure"}],
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/quick",
                "messages": [{"role": "user", "content": "test 500"}],
                "stream": False,
            },
        )
        # Should get a 500 or 502 with error details
        assert resp.status_code in (500, 502), f"Expected 500/502, got {resp.status_code}"
        body = resp.json()
        assert "error" in body

    def test_empty_messages(self, orchestrator_server, backend_target, backend_client):
        """Empty messages array is handled gracefully."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/quick",
                "messages": [],
                "stream": False,
            },
        )
        # Should return 200 (with whatever response) or 400 (validation error)
        assert resp.status_code in (200, 400, 422), f"Got {resp.status_code}: {resp.text[:200]}"

    def test_missing_messages_field(self, orchestrator_server, backend_target, backend_client):
        """Missing messages field is handled gracefully."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/quick",
                "stream": False,
            },
        )
        assert resp.status_code in (200, 400, 422), f"Got {resp.status_code}: {resp.text[:200]}"

    def test_nonexistent_model(self, orchestrator_server, backend_target, backend_client):
        """Unknown model falls back or returns sensible error."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "completely/unknown/model/xyz",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        # Should return 200 (catchall route) or 404
        assert resp.status_code in (200, 404), f"Got {resp.status_code}: {resp.text[:200]}"

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, orchestrator_server, backend_target):
        """Multiple concurrent requests all complete successfully."""
        url = f"{orchestrator_server.base_url}/v1/chat/completions"
        payload = {
            "model": "local/quick",
            "messages": [{"role": "user", "content": "concurrent test"}],
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            tasks = [client.post(url, json=payload) for _ in range(5)]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

        non_exceptions = [r for r in responses if not isinstance(r, BaseException)]
        assert len(non_exceptions) == 5, f"Only {len(non_exceptions)} of 5 completed"
        for r in non_exceptions:
            assert r.status_code == 200, f"Got {r.status_code}"
