"""Cancellation observability at the streaming HTTP boundary."""

from __future__ import annotations

import asyncio
import time

import pytest

from keeprollming.observability import EventDispatcher


@pytest.mark.asyncio
async def test_cancelled_stream_emits_downstream_closed_and_reraises() -> None:
    """ASGI cancellation is observable but must not become an SSE error."""
    from keeprollming.endpoints.streaming_handlers import process_streaming_request

    received = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe("execution.streaming", received.append)

    class Route:
        name = "test/cancelled"
        filters = None
        upstream_url = "http://upstream.invalid"
        _route_hierarchy = [name]

    class Pipeline:
        async def run_stream(self, *_args, **_kwargs):
            yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
            raise asyncio.CancelledError

    generator = process_streaming_request(
        url="http://upstream.invalid/v1/chat/completions",
        client=None,
        payload={"model": "test", "messages": [], "stream": True},
        route_headers={},
        route=Route(),
        req_id="cancelled-stream",
        request_timeout=30.0,
        fallback_attempts=[],
        visited_models=set(),
        upstream_model="test",
        is_passthrough=False,
        transform_reasoning_content=False,
        add_empty_content_when_reasoning_only=False,
        reasoning_placeholder="",
        t_start=time.perf_counter(),
        pipeline=Pipeline(),
        dispatcher=dispatcher,
    )

    assert await anext(generator) == b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
    with pytest.raises(asyncio.CancelledError):
        await anext(generator)

    closed = [event for event in received if event.type == "execution.streaming.downstream_closed"]
    assert len(closed) == 1
    assert closed[0].data == {"reason": "cancelled", "chunks_yielded": 1}


@pytest.mark.asyncio
async def test_keepalive_timeout_does_not_cancel_upstream_reader() -> None:
    """A keepalive is not a stream cancellation boundary."""
    from keeprollming.endpoints.streaming_handlers import _interleave_keepalive

    release = asyncio.Event()

    async def upstream():
        await release.wait()
        yield b"data: upstream\n\n"

    wrapped = _interleave_keepalive(upstream(), interval=0.001)
    assert await anext(wrapped) == b": keepalive\n\n"

    release.set()
    assert await asyncio.wait_for(anext(wrapped), timeout=0.1) == b"data: upstream\n\n"
    await wrapped.aclose()


@pytest.mark.asyncio
async def test_keepalive_close_cancels_pending_upstream_reader() -> None:
    """Downstream closure must not leak a pending httpx/body-reader task."""
    from keeprollming.endpoints.streaming_handlers import _interleave_keepalive

    cancelled = asyncio.Event()

    async def upstream():
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        except asyncio.CancelledError:
            cancelled.set()
            raise

    wrapped = _interleave_keepalive(upstream(), interval=0.001)
    assert await anext(wrapped) == b": keepalive\n\n"
    await wrapped.aclose()

    await asyncio.wait_for(cancelled.wait(), timeout=0.1)


@pytest.mark.asyncio
async def test_keepalive_does_not_read_ahead_of_downstream_consumer() -> None:
    """An upstream read begins only after the prior downstream yield resumes."""
    from keeprollming.endpoints.streaming_handlers import _interleave_keepalive

    second_read_started = asyncio.Event()

    async def upstream():
        yield b"data: first\n\n"
        second_read_started.set()
        yield b"data: second\n\n"

    wrapped = _interleave_keepalive(upstream(), interval=1.0)
    assert await anext(wrapped) == b"data: first\n\n"
    assert not second_read_started.is_set()

    assert await anext(wrapped) == b"data: second\n\n"
    assert second_read_started.is_set()
    await wrapped.aclose()


@pytest.mark.asyncio
async def test_keepalive_close_closes_upstream_paused_at_prior_yield() -> None:
    """Closing after a yielded chunk must close an upstream iterator with no read pending."""
    from keeprollming.endpoints.streaming_handlers import _interleave_keepalive

    upstream_closed = asyncio.Event()

    async def upstream():
        try:
            yield b"data: first\n\n"
            await asyncio.Event().wait()
        finally:
            upstream_closed.set()

    wrapped = _interleave_keepalive(upstream(), interval=1.0)
    assert await anext(wrapped) == b"data: first\n\n"
    await wrapped.aclose()

    await asyncio.wait_for(upstream_closed.wait(), timeout=0.1)


@pytest.mark.asyncio
async def test_sse_response_cancels_body_on_disconnect_for_asgi_24(monkeypatch) -> None:
    """Peer disconnect wins even where Starlette would otherwise wait for send failure."""
    import keeprollming.app as app_module
    from keeprollming.app import DisconnectAwareStreamingResponse
    from keeprollming.observability import EventDispatcher

    body_closed = asyncio.Event()
    disconnect = asyncio.Event()
    sent: list[dict] = []
    received = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe("execution.streaming", received.append)
    dispatcher.subscribe("request.lifecycle", received.append)
    monkeypatch.setattr(app_module, "_event_dispatcher", dispatcher)

    async def body():
        try:
            yield b"data: first\n\n"
            await asyncio.Event().wait()
        finally:
            body_closed.set()

    async def receive() -> dict:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)
        if message.get("type") == "http.response.body":
            disconnect.set()

    response = DisconnectAwareStreamingResponse(body(), media_type="text/event-stream")
    await response(
        {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send,
    )

    assert any(message.get("type") == "http.response.body" for message in sent)
    assert body_closed.is_set()
    peer_events = [
        event
        for event in received
        if event.type == "execution.streaming.peer_disconnected"
    ]
    assert len(peer_events) == 1
    assert peer_events[0].data["asgi_spec"] == "2.4"
    cancelled = [event for event in received if event.type == "request.lifecycle.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].level == "BASIC"
    assert cancelled[0].data["reason"] == "http.disconnect"


@pytest.mark.asyncio
async def test_sse_response_records_send_failure_as_downstream_abort(monkeypatch) -> None:
    """A failed ASGI send has the same terminal semantics as a disconnect."""
    import keeprollming.app as app_module
    from keeprollming.app import DisconnectAwareStreamingResponse
    from keeprollming.observability import EventDispatcher

    received = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe("execution.streaming", received.append)
    dispatcher.subscribe("request.lifecycle", received.append)
    monkeypatch.setattr(app_module, "_event_dispatcher", dispatcher)

    async def body():
        yield b"data: first\n\n"

    async def receive() -> dict:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message.get("type") == "http.response.body":
            raise OSError("broken pipe")

    response = DisconnectAwareStreamingResponse(body(), media_type="text/event-stream")
    await response(
        {
            "type": "http",
            "asgi": {"spec_version": "2.4"},
            "state": {"req_id": "send-failure"},
        },
        receive,
        send,
    )

    cancelled = [event for event in received if event.type == "request.lifecycle.cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0].level == "BASIC"
    assert cancelled[0].data["reason"] == "downstream_send_error"
    peer_events = [
        event
        for event in received
        if event.type == "execution.streaming.peer_disconnected"
    ]
    assert len(peer_events) == 1
    assert peer_events[0].data == {
        "reason": "downstream_send_error",
        "asgi_spec": "2.4",
        "action": "body_stream_send_failed",
    }
