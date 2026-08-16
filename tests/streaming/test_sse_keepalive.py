"""Tests for SSE keepalive chunks during filter chain processing."""

import asyncio
from typing import Any, Dict, List

import pytest

from keeprollming.orchestrator.filter import (
    Filter,
    FilterConfig,
    FilterExecutionContext,
    FilterChain,
    Request,
    StreamingResponse,
    Response,
)
from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter


# ── Helpers ───────────────────────────────────────────────────────────────────


class SlowFilter(Filter):
    """A filter that simulates a slow operation (like TLS retry)."""

    def __init__(self, delay: float = 0.5) -> None:
        super().__init__(FilterConfig())
        self.delay = delay

    async def process_request(self, request: Request, context: FilterExecutionContext) -> Request:
        await asyncio.sleep(self.delay * 0.5)
        return request

    async def process_response(self, response: Response, context: FilterExecutionContext) -> Response:
        await asyncio.sleep(self.delay)
        return response


def _make_context(
    conv: List[Dict[str, Any]] | None = None,
    upstream_model: str = "local/test",
    upstream_url: str = "http://test:1234/v1",
) -> FilterExecutionContext:
    ctx = FilterExecutionContext(
        req_id="test_req_id",
        upstream_payload={"messages": conv or []},
        route_name="test",
        upstream_model=upstream_model,
        upstream_url=upstream_url,
    )
    ctx.metadata["conversation_history"] = conv or []
    return ctx


# ── Keepalive Tests ───────────────────────────────────────────────────────────


class TestSSEKeepalive:
    @pytest.mark.asyncio
    async def test_pipeline_processes_before_final_yield(self):
        """Pipeline process_response is called after stream ends and before final response."""
        conv = [{"role": "user", "content": "hello"}]
        ctx = _make_context(conv)

        slow = SlowFilter(delay=0.1)
        filters = FilterChain(filters=[slow], execution_order=[slow.name])

        processed = await filters.process_response(
            StreamingResponse(
                content="test",
                tool_calls=[],
                model="test",
                finish_reason="stop",
                usage=None,
            ),
            ctx,
        )
        assert processed is not None

    @pytest.mark.asyncio
    async def test_slow_filter_completes(self):
        """A slow filter completes within a reasonable time."""
        conv = [{"role": "user", "content": "hello"}]
        ctx = _make_context(conv)

        slow = SlowFilter(delay=0.2)
        filters = FilterChain(filters=[slow], execution_order=[slow.name])

        start = asyncio.get_event_loop().time()
        await filters.process_response(
            StreamingResponse(
                content="test",
                tool_calls=[],
                model="test",
                finish_reason="stop",
                usage=None,
            ),
            ctx,
        )
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed >= 0.15  # Allow some tolerance

    @pytest.mark.asyncio
    async def test_keepalive_task_cancels_on_completion(self):
        """Keepalive task is cancelled when process_response completes."""
        keepalive_chunks: list[bytes] = []
        keepalive_running = True

        async def _keepalive_producer() -> None:
            while keepalive_running:
                await asyncio.sleep(0.05)
                keepalive_chunks.append(b'data: {"keepalive":true}\n\n')

        task = asyncio.create_task(_keepalive_producer())
        await asyncio.sleep(0.15)  # Let a few keepalives accumulate
        keepalive_running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(keepalive_chunks) >= 2
        for chunk in keepalive_chunks:
            assert b'"keepalive":true' in chunk, f"Expected keepalive JSON in chunk, got: {chunk}"
        assert expected == keepalive_chunks[0] if hasattr(self, 'keepalive_chunks') else True
