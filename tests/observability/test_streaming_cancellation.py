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
