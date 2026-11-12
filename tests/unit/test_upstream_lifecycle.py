"""Actual loopback HTTP transport phases; no external servers or GPU calls."""

import asyncio
import json
import time

import httpx
import pytest

from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.route_status import RouteStatusRegistry
from keeprollming.upstream.lifecycle import UpstreamLifecycle


def test_trace_emits_transitions_not_bytes_headers_or_duplicate_phases():
    async def run():
        dispatcher = EventDispatcher()
        events = []
        dispatcher.subscribe("execution.upstream", events.append)
        lifecycle = UpstreamLifecycle("transport", dispatcher)
        for name in (
            "connect_tcp.started",
            "send_request_headers.started",
            "send_request_body.started",
            "send_request_body.complete",
            "receive_response_headers.complete",
        ):
            await lifecycle.trace("http11." + name, {"headers": "credential-secret", "body": "private-prompt"})
        assert [e.data["phase"] for e in events] == [
            "connecting",
            "sending",
            "waiting_response_headers",
            "response_headers",
        ]
        assert "credential-secret" not in str(events) and "private-prompt" not in str(events)
        assert all(e.req_id == "transport" and e.level == "DEBUG" for e in events)

    asyncio.run(run())


def test_transport_failure_reports_last_phase_without_exception_content():
    async def run():
        dispatcher = EventDispatcher()
        events = []
        dispatcher.subscribe("execution.upstream", events.append)
        lifecycle = UpstreamLifecycle("failed", dispatcher)
        lifecycle.mark("waiting_response_headers")
        await lifecycle.trace("http11.receive_response_headers.failed", {"exception": TimeoutError("private-content")})
        assert events[-1].level == "BASIC"
        assert events[-1].data["phase"] == "waiting_response_headers"
        assert events[-1].data["error_type"] == "TimeoutError"
        assert "private-content" not in str(events)

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_real_transport_and_status_distinguish_sending_from_waiting_headers(monkeypatch, stream):
    from keeprollming import app
    from keeprollming.endpoints.chat_completions import _post_upstream
    from keeprollming.endpoints.streaming_handlers import process_streaming_request
    from keeprollming.types import Route

    async def run():
        dispatcher = EventDispatcher()
        registry = RouteStatusRegistry()
        events = []
        dispatcher.subscribe("execution.upstream", events.append)
        dispatcher.subscribe("execution.upstream", registry)
        registry(
            RuntimeEvent(
                type="execution.chat.route_resolved",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="execution", component="test"),
                req_id="transport",
                data={"resolved_route": "test"},
            )
        )
        monkeypatch.setattr(app, "get_event_dispatcher", lambda: dispatcher)
        received = asyncio.Event()
        release = asyncio.Event()

        async def server(reader, writer):
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(h.split(b":", 1)[1]) for h in headers.split(b"\r\n") if h.lower().startswith(b"content-length:")
                )
                await reader.readexactly(length)
                received.set()
                await release.wait()
                if stream:
                    body = (
                        b'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
                        b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                        b"data: [DONE]\n\n"
                    )
                else:
                    body = json.dumps({"ok": True}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: "
                    + (b"text/event-stream" if stream else b"application/json")
                    + b"\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        listener = await asyncio.start_server(server, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
        async with listener, httpx.AsyncClient(trust_env=False) as client:

            async def request():
                payload = {"model": "m", "messages": [{"role": "user", "content": "hello"}], "stream": stream}
                if not stream:
                    return await _post_upstream(
                        client, url, json=payload, headers={}, request_timeout=5, req_id="transport"
                    )
                return [
                    chunk
                    async for chunk in process_streaming_request(
                        url=url,
                        client=client,
                        payload=payload,
                        route_headers={},
                        route=Route(name="test", pattern="test", upstream_url=url, model="m"),
                        req_id="transport",
                        request_timeout=5,
                        fallback_attempts=[],
                        visited_models=set(),
                        upstream_model="m",
                        is_passthrough=False,
                        transform_reasoning_content=False,
                        add_empty_content_when_reasoning_only=False,
                        reasoning_placeholder="",
                        t_start=time.perf_counter(),
                        dispatcher=dispatcher,
                    )
                ]

            task = asyncio.create_task(request())
            try:
                await asyncio.wait_for(received.wait(), 2)
                # Server has the whole body but deliberately withholds headers.
                for _ in range(100):
                    if any(e.data.get("phase") == "waiting_response_headers" for e in events):
                        break
                    await asyncio.sleep(0.01)
                assert not task.done()
                status = registry.snapshot("test")["pending_requests"]
                assert status[0]["phase"] == "waiting_response_headers"
                release.set()
                await asyncio.wait_for(task, 3)
                phases = [e.data.get("phase") for e in events if e.type == "execution.upstream.phase"]
                assert phases == [
                    "dispatching",
                    "connecting",
                    "sending",
                    "waiting_response_headers",
                    "response_headers",
                ]
                assert all(e.req_id == "transport" for e in events)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
