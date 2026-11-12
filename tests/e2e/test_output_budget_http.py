"""HTTP boundary regression: assert actual upstream JSON in both serving modes."""

import json

import httpx
import pytest

from keeprollming import config
from keeprollming.app import app
from keeprollming.circuit_breaker import CircuitBreakerRegistry
from keeprollming.endpoints import chat_completions as chat
from keeprollming.routing.resources import backend_resource_key
from keeprollming.types import DefaultSettings, Route


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("client_limit", "route_limit", "override", "preserved", "expected", "prompt_tokens"),
    [
        (None, 65384, None, None, 65384, 50),
        (None, None, None, None, 8192, 50),
        (1000, 65384, None, None, 1000, 50),
        (1000, 65384, 4096, None, 4096, 50),
        (1000, 65384, 4096, 7777, 7777, 50),
        (0, 65384, None, None, None, 50),
        (300000, 65384, None, None, 261966, 50),
        (None, 65384, None, None, None, 262144),
    ],
)
async def test_http_transmits_effective_output_budget(
    monkeypatch, stream, client_limit, route_limit, override, preserved, expected, prompt_tokens
):
    base = Route(
        name="base",
        pattern="base",
        _is_private=True,
        model="test-model",
        upstream_url="http://backend.invalid",
        ctx_len=262144,
        max_tokens=route_limit,
        summary_enabled=False,
        max_concurrent=1,
        circuit_breaker_enabled=True,
        recovery_timeout=1,
    )
    route = Route(
        name="budget", pattern="budget", extends="base", overrides={"max_tokens": override} if override else {}
    )
    routes = [base, route]
    client_model = "budget"
    if preserved is not None:
        routes.append(
            Route(
                name="balanced",
                pattern="balanced",
                overrides={"max_tokens": preserved},
                balance={"targets": ["budget"], "preserve_overrides": ["max_tokens"]},
            )
        )
        client_model = "balanced"
    defaults = DefaultSettings(ctx_len=262144, max_tokens=8192)
    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat, "USER_ROUTES", routes)
    monkeypatch.setattr(config, "DEFAULTS", defaults)
    monkeypatch.setattr(chat, "DEFAULTS", defaults)
    monkeypatch.setattr(config, "CONFIG", {"client_access": {"default": "any"}})
    monkeypatch.setattr(config, "DEFAULT_MAX_COMPLETION_TOKENS", 77)
    monkeypatch.setattr(chat, "_count_tokens_safe", lambda _: prompt_tokens)
    clock = [0.0]
    circuit = CircuitBreakerRegistry(clock=lambda: clock[0])
    monkeypatch.setattr("keeprollming.circuit_breaker.get_circuit_breaker_registry", lambda: circuit)
    resource = str(backend_resource_key(base))
    if expected is None and prompt_tokens >= 262144:
        failed = circuit.allow(
            route_name="budget", enabled=True, recovery_timeout=1, resource_key=resource, req_id="failed"
        )
        circuit.record_failure(failed, req_id="failed", failure_threshold=1)
        clock[0] = 2.0
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        if stream:
            data = {
                "id": "test",
                "object": "chat.completion.chunk",
                "model": "test-model",
                "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
            }
            return httpx.Response(
                200,
                content=f"data: {json.dumps(data)}\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "model": "test-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream:

        async def get_client(*_args, **_kwargs):
            return upstream

        monkeypatch.setattr(chat, "http_client", get_client)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://krm.invalid") as client:
            payload = {"model": client_model, "messages": [{"role": "user", "content": "hello"}], "stream": stream}
            if client_limit is not None:
                payload["max_tokens"] = client_limit
            response = await client.post("/v1/chat/completions", json=payload)
            if expected is None:
                assert response.status_code == 400, response.text
                assert sent == []
                # A rejected request must not hold admission or a half-open
                # probe; the next valid request on the same resource succeeds.
                assert circuit.is_available(
                    route_name="budget", resource_key=resource, enabled=True, recovery_timeout=1
                )
                monkeypatch.setattr(chat, "_count_tokens_safe", lambda _: 50)
                retry = await client.post("/v1/chat/completions", json={**payload, "max_tokens": 256})
                assert retry.status_code == 200, retry.text
    if expected is None:
        assert response.status_code == 400, response.text
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert len(sent) == 1 and sent[0]["max_tokens"] == 256
        return
    assert response.status_code == 200, response.text
    assert len(sent) == 1
    assert sent[0]["max_tokens"] == expected
    assert "ok" in response.text
