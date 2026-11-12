"""Process-level contracts for session reservations across real upstreams."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest


@pytest.mark.e2e_fake
@pytest.mark.parametrize("balanced_routing_servers", ["priority", "least_loaded"], indirect=True)
@pytest.mark.parametrize("stream", [False, True])
def test_concurrent_assignment_with_spare_capacity(balanced_routing_servers, stream, request):
    """Actual HTTP selection must spread work before capacity is exhausted."""
    servers = balanced_routing_servers
    base = servers.orchestrator.base_url
    least_loaded = request.node.callspec.params["balanced_routing_servers"] == "least_loaded"

    async def run():
        async with httpx.AsyncClient(timeout=15) as client:
            for backend, content in ((servers.fast, "FAST"), (servers.slow, "SLOW")):
                response = await client.post(
                    f"{backend.base_url}/__scenario",
                    json={"scenario": {"chat": {"content": content, "stream_pieces": [content]}}},
                )
                response.raise_for_status()
            (
                await client.post(f"{servers.fast.base_url}/__control", json={"action": "delay", "ms": 4000})
            ).raise_for_status()

            async def send(session):
                response = await client.post(
                    f"{base}/v1/chat/completions",
                    headers={"x-librechat-conversation-id": session},
                    json={"model": "public/main", "stream": stream, "messages": [{"role": "user", "content": session}]},
                )
                response.raise_for_status()
                return _stream_content(response) if stream else _content(response)

            first = asyncio.create_task(send("first-concurrent"))
            try:
                for _ in range(100):
                    status = await client.get(f"{base}/routes")
                    status.raise_for_status()
                    if any(route["admission"]["resource"]["active"] for route in status.json()["routes"]):
                        break
                    if first.done():
                        pytest.fail(f"First request finished before selection: {await first}")
                    await asyncio.sleep(0.02)
                else:
                    pytest.fail("First request did not acquire its backend slot")
                assert not first.done(), "First request must still be active during selection"
                second = await send("second-concurrent")
                calls = (await client.get(f"{servers.slow.base_url}/__stats")).json()["calls_total"]
                assert second == ("SLOW" if least_loaded else "FAST")
                assert calls == (1 if least_loaded else 0)
                assert await first == "FAST"
            finally:
                if not first.done():
                    first.cancel()
                await asyncio.gather(first, return_exceptions=True)

    asyncio.run(run())


def _content(response: httpx.Response) -> str:
    return response.json()["choices"][0]["message"]["content"]


def _stream_content(response: httpx.Response) -> str:
    result: list[str] = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            continue
        delta = json.loads(payload)["choices"][0]["delta"]
        result.append(delta.get("content", ""))
    return "".join(result)


@pytest.mark.e2e_fake
@pytest.mark.parametrize("stream", [False, True])
def test_session_reservation_sends_other_session_to_next_backend(
    balanced_routing_servers,
    stream: bool,
) -> None:
    """A completed code turn leases fast; public work uses slow in either mode."""
    fast = balanced_routing_servers.fast
    slow = balanced_routing_servers.slow
    base = balanced_routing_servers.orchestrator.base_url
    httpx.post(
        f"{fast.base_url}/__scenario",
        json={"scenario": {"chat": {"content": "FAST", "stream_pieces": ["FAST"]}}},
    ).raise_for_status()
    httpx.post(
        f"{slow.base_url}/__scenario",
        json={"scenario": {"chat": {"content": "SLOW", "stream_pieces": ["SLOW"]}}},
    ).raise_for_status()

    with httpx.Client(timeout=10.0) as client:
        first = client.post(
            f"{base}/v1/chat/completions",
            headers={"x-librechat-conversation-id": "code-session"},
            json={
                "model": "code/main",
                "stream": stream,
                "messages": [{"role": "user", "content": "first"}],
            },
        )
        first.raise_for_status()
        assert (_stream_content(first) if stream else _content(first)) == "FAST"

        second = client.post(
            f"{base}/v1/chat/completions",
            headers={"x-librechat-conversation-id": "public-session"},
            json={
                "model": "public/main",
                "stream": False,
                "messages": [{"role": "user", "content": "second"}],
            },
        )
        second.raise_for_status()
        assert _content(second) == "SLOW"

    assert httpx.get(f"{fast.base_url}/__stats").json()["calls_total"] == 1
    assert httpx.get(f"{slow.base_url}/__stats").json()["calls_total"] == 1


@pytest.mark.e2e_fake
def test_short_turn_below_reservation_threshold_does_not_hold_fast_backend(
    balanced_routing_servers,
) -> None:
    """A small auxiliary turn must not create a session lease."""
    fast = balanced_routing_servers.fast
    slow = balanced_routing_servers.slow
    base = balanced_routing_servers.orchestrator.base_url
    httpx.post(
        f"{fast.base_url}/__scenario",
        json={"scenario": {"chat": {"content": "FAST", "stream_pieces": ["FAST"]}}},
    ).raise_for_status()
    httpx.post(
        f"{slow.base_url}/__scenario",
        json={"scenario": {"chat": {"content": "SLOW", "stream_pieces": ["SLOW"]}}},
    ).raise_for_status()

    with httpx.Client(timeout=10.0) as client:
        auxiliary = client.post(
            f"{base}/v1/chat/completions",
            headers={"x-librechat-conversation-id": "title-session"},
            json={
                "model": "warm/main",
                "stream": False,
                "messages": [{"role": "user", "content": "title"}],
            },
        )
        auxiliary.raise_for_status()
        assert _content(auxiliary) == "FAST"

        public = client.post(
            f"{base}/v1/chat/completions",
            headers={"x-librechat-conversation-id": "another-session"},
            json={
                "model": "public/main",
                "stream": False,
                "messages": [{"role": "user", "content": "normal"}],
            },
        )
        public.raise_for_status()
        assert _content(public) == "FAST"

    assert httpx.get(f"{fast.base_url}/__stats").json()["calls_total"] == 2
    assert httpx.get(f"{slow.base_url}/__stats").json()["calls_total"] == 0


@pytest.mark.e2e_fake
def test_capability_enforcement_is_optional_over_real_http(
    balanced_routing_servers,
) -> None:
    """Advisory metadata keeps target order; strict metadata selects tools."""
    fast = balanced_routing_servers.fast
    slow = balanced_routing_servers.slow
    base = balanced_routing_servers.orchestrator.base_url
    httpx.post(
        f"{fast.base_url}/__scenario",
        json={"scenario": {"chat": {"content": "TEXT", "stream_pieces": ["TEXT"]}}},
    ).raise_for_status()
    httpx.post(
        f"{slow.base_url}/__scenario",
        json={"scenario": {"chat": {"content": "TOOLS", "stream_pieces": ["TOOLS"]}}},
    ).raise_for_status()
    payload = {
        "stream": False,
        "messages": [{"role": "user", "content": "read it"}],
        "tools": [{"type": "function", "function": {"name": "read_file"}}],
    }

    with httpx.Client(timeout=10.0) as client:
        advisory = client.post(
            f"{base}/v1/chat/completions",
            json={**payload, "model": "capability/advisory"},
        )
        strict = client.post(
            f"{base}/v1/chat/completions",
            json={**payload, "model": "capability/strict"},
        )

    advisory.raise_for_status()
    strict.raise_for_status()
    assert _content(advisory) == "TEXT"
    assert _content(strict) == "TOOLS"
