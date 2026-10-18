"""E2E tests for error handling scenarios.

Verifies upstream timeout, fallback chain, empty messages,
and concurrent requests.
"""

import asyncio
import json

import httpx
import pytest


async def _wait_for_active_stream(
    client: httpx.AsyncClient,
    stats_url: str,
    *,
    timeout: float = 5.0,
) -> dict:
    """Wait until the fake fallback has an open real upstream stream."""
    deadline = asyncio.get_running_loop().time() + timeout
    latest: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(stats_url)
        response.raise_for_status()
        latest = response.json()
        if latest["active_streams"] == 1:
            return latest
        await asyncio.sleep(0.02)
    raise AssertionError(f"fallback stream did not become active: {latest}")


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

    def test_upstream_500_non_summary(
        self, orchestrator_server, backend_target, backend_client, configure_fake_backend
    ):
        """Upstream returns 500 on normal request → error propagated to client."""
        configure_fake_backend(
            {
                "chat": {
                    "script": [{"type": "error", "status": 500, "message": "temporary backend failure"}],
                }
            }
        )

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

    def test_upstream_500_streaming_returns_upstream_error_not_empty_assistant(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
    ):
        """A pre-stream upstream failure is relayed as a semantic SSE error.

        The downstream HTTP response is already an SSE response by the time
        the upstream status is observed, so its status can remain 200. The
        important invariant is that clients receive the actual upstream error
        and never a synthetic empty assistant response with ``stop``.
        """
        configure_fake_backend(
            {
                "chat": {
                    "script": [{"type": "error", "status": 500, "message": "temporary backend failure"}],
                }
            }
        )

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/quick",
                "messages": [{"role": "user", "content": "test streamed 500"}],
                "stream": True,
            },
        )

        assert resp.status_code == 200
        events = [
            json.loads(line.removeprefix("data: "))
            for line in resp.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]
        assert events == [{"error": {"message": "temporary backend failure"}}]
        assert '"finish_reason": "stop"' not in resp.text

    @pytest.mark.asyncio
    async def test_route_admission_rejects_second_stream_while_first_is_active(
        self,
        orchestrator_server,
        backend_target,
        configure_fake_backend,
    ):
        """A route slot remains held for the complete streaming response."""
        configure_fake_backend(
            {
                "chat": {"content": "slow response", "ttft_ms": 750},
            }
        )
        url = f"{orchestrator_server.base_url}/v1/chat/completions"
        payload = {
            "model": "internal/admission",
            "messages": [{"role": "user", "content": "hold this route"}],
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            async with client.stream("POST", url, json=payload) as first:
                assert first.status_code == 200
                second = await client.post(url, json=payload)
                assert second.status_code == 429
                assert second.json()["error"]["code"] == "route_capacity_exceeded"
                await first.aread()

            after_release = await client.post(url, json=payload)
        assert after_release.status_code == 200

    def test_balanced_route_selects_tool_capable_target(
        self,
        orchestrator_server,
        backend_target,
        backend_client,
        configure_fake_backend,
        get_fake_stats,
    ):
        """P5 eligibility reaches the real HTTP upstream model selection."""
        configure_fake_backend({"chat": {"content": "tool capable"}})
        response = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/capability-selector",
                "messages": [{"role": "user", "content": "read the file"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "read_file", "parameters": {"type": "object"}},
                    }
                ],
                "stream": False,
            },
        )
        assert response.status_code == 200
        stats = get_fake_stats()
        assert stats["calls_by_model"] == {"test-capability-tools-model": 1}

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


@pytest.mark.asyncio
async def test_fallback_transfers_admission_to_its_real_backend(
    fallback_admission_servers,
):
    """A live fallback stream prevents a concurrent hidden overcommit.

    Request one fails on primary then holds the secondary SSE stream. Request
    two reaches the same primary failure but must be denied before it opens a
    second connection to the saturated fallback backend.
    """
    primary = fallback_admission_servers.primary.base_url
    secondary = fallback_admission_servers.secondary.base_url
    orchestrator = fallback_admission_servers.orchestrator.base_url
    httpx.post(
        f"{primary}/__scenario",
        json={
            "scenario": {
                "chat": {
                    "script": [
                        {"type": "error", "status": 503, "message": "primary unavailable"},
                        {"type": "error", "status": 503, "message": "primary unavailable"},
                    ]
                }
            }
        },
        timeout=5.0,
    ).raise_for_status()
    httpx.post(
        f"{secondary}/__scenario",
        json={
            "scenario": {
                "chat": {
                    "stream_pieces": ["secondary response"],
                    "ttft_ms": 1500,
                }
            }
        },
        timeout=5.0,
    ).raise_for_status()

    payload = {
        "model": "internal/fallback-admission",
        "messages": [{"role": "user", "content": "exercise fallback admission"}],
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        async with client.stream(
            "POST",
            f"{orchestrator}/v1/chat/completions",
            json=payload,
        ) as first:
            assert first.status_code == 200
            first_chunks = first.aiter_bytes()
            first_chunk = asyncio.create_task(anext(first_chunks))
            await _wait_for_active_stream(client, f"{secondary}/__stats")

            second = await client.post(
                f"{orchestrator}/v1/chat/completions",
                json={**payload, "stream": False},
            )
            assert second.status_code >= 500

            secondary_stats = (await client.get(f"{secondary}/__stats")).json()
            assert secondary_stats["calls_total"] == 1
            assert secondary_stats["active_streams"] == 1
            assert b"secondary response" in await first_chunk
            async for _chunk in first_chunks:
                pass

    finished = httpx.get(f"{secondary}/__stats", timeout=5.0).json()
    assert finished["streams_started"] == 1
    assert finished["streams_closed"] == 1
