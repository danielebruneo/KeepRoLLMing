"""Real streaming error delivery must distinguish client and backend failures."""

import asyncio
import json
import time
from unittest.mock import Mock

import httpx
import pytest

from keeprollming.circuit_breaker import CircuitBreakerRegistry
from keeprollming.endpoints.streaming_handlers import process_streaming_request
from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.orchestrator.pipeline import Pipeline
from keeprollming.types import Route


@pytest.mark.parametrize(
    ("status", "message", "backend_failure"),
    [
        (400, "invalid temperature", False),
        (400, "maximum context length exceeded", False),
        (400, "cudaMemGetInfo: unspecified launch failure", True),
        (400, "CUDA error: device is lost", True),
        (404, "model unavailable", True),
        (500, "internal error", True),
        (503, "temporarily unavailable", True),
        (None, "upstream read timeout", True),
    ],
)
def test_streaming_error_health_accounting(status, message, backend_failure):
    """Errors stay visible, but repeated invalid requests cannot open a breaker."""

    async def run():
        envelope = {"error": {"message": message, "type": "invalid_request_error"}}
        calls = []

        def respond(request):
            calls.append(request)
            if status is None:
                raise httpx.ReadTimeout(message, request=request)
            return httpx.Response(status, json=envelope)

        registry = CircuitBreakerRegistry()
        attempt = registry.allow(route_name="test", enabled=True, recovery_timeout=60, req_id="test")
        failure = Mock(side_effect=lambda: registry.record_failure(attempt, req_id="test", failure_threshold=2))
        success = Mock(side_effect=lambda: registry.record_success(attempt, req_id="test"))
        dispatcher = EventDispatcher()
        events = []
        dispatcher.subscribe("execution", events.append)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for _ in range(2):
                frames = [
                    frame
                    async for frame in process_streaming_request(
                        url="http://test.invalid/v1/chat/completions",
                        client=client,
                        payload={"model": "test", "messages": [{"role": "user", "content": "test"}], "stream": True},
                        route_headers={},
                        route=Route(name="test", pattern="test", model="test"),
                        req_id="test",
                        request_timeout=5,
                        fallback_attempts=[],
                        visited_models=set(),
                        upstream_model="test",
                        is_passthrough=False,
                        transform_reasoning_content=False,
                        add_empty_content_when_reasoning_only=False,
                        reasoning_placeholder="",
                        t_start=time.perf_counter(),
                        pipeline=Pipeline.from_route_config({}),
                        dispatcher=dispatcher,
                        on_upstream_failure=failure,
                        on_upstream_success=success,
                    )
                ]
                assert len(frames) == 2
                assert frames[-1] == b"data: [DONE]\n\n"
                error = json.loads(frames[0].decode().removeprefix("data: "))
                if status is not None:
                    assert error == envelope
                else:
                    assert error["error"]["message"] == message

        assert len(calls) == 2  # No retries or hidden successful requests.
        assert failure.call_count == (2 if backend_failure else 0)
        assert success.call_count == (0 if backend_failure else 2)
        assert registry.snapshot("test") == {
            "state": "open" if backend_failure else "closed",
            "consecutive_failures": 2 if backend_failure else 0,
        }
        completions = [event for event in events if event.type == "execution.performance.request_complete"]
        assert len(completions) == 2
        assert all(event.data["finish_reason"] == "error" for event in completions)

    asyncio.run(run())


def test_client_error_completes_half_open_probe():
    """A reachable backend rejecting a request must not strand its probe."""

    async def run():
        now = [0.0]
        registry = CircuitBreakerRegistry(clock=lambda: now[0])
        attempt = registry.allow(route_name="test", enabled=True, recovery_timeout=1, req_id="test")
        registry.record_failure(attempt, req_id="test", failure_threshold=1)
        now[0] = 2.0
        probe = registry.allow(route_name="test", enabled=True, recovery_timeout=1, req_id="probe")
        assert registry.snapshot("test")["state"] == "half_open"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(400, json={"error": {"message": "bad request"}}))
        ) as client:
            frames = [
                frame
                async for frame in process_streaming_request(
                    url="http://test.invalid/v1/chat/completions",
                    client=client,
                    payload={"model": "test", "messages": [], "stream": True},
                    route_headers={},
                    route=Route(name="test", pattern="test", model="test"),
                    req_id="probe",
                    request_timeout=5,
                    fallback_attempts=[],
                    visited_models=set(),
                    upstream_model="test",
                    is_passthrough=False,
                    transform_reasoning_content=False,
                    add_empty_content_when_reasoning_only=False,
                    reasoning_placeholder="",
                    t_start=time.perf_counter(),
                    pipeline=Pipeline.from_route_config({}),
                    on_upstream_success=lambda: registry.record_success(probe, req_id="probe"),
                    on_upstream_failure=lambda: registry.record_failure(probe, req_id="probe", failure_threshold=1),
                )
            ]
        assert b"bad request" in frames[0]
        assert registry.snapshot("test") == {"state": "closed", "consecutive_failures": 0}
        assert registry.allow(route_name="test", enabled=True, recovery_timeout=1, req_id="next") is not None

    asyncio.run(run())
