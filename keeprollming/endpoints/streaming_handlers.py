"""Streaming request processing utilities for chat completions.

This module contains the streaming-specific logic for handling SSE responses
and delegating to the filter chain.
"""

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Set

import httpx

from ..logger import log
from ..routing import Route

# Import filter chain for streaming support
try:
    from ..orchestrator.pipeline import Pipeline
    from ..orchestrator.filter import FilterChain, FilterExecutionContext, StreamingResponse
    FILTER_CHAIN_AVAILABLE = True
except ImportError:
    FILTER_CHAIN_AVAILABLE = False

# Keepalive interval (seconds) — configurable for debugging.
# Set to 0 or None to disable keepalive entirely.
_SSE_KEEPALIVE_INTERVAL: float = 15.0


async def _interleave_keepalive(
    gen: AsyncIterator[bytes],
    interval: float | None = None,
) -> AsyncIterator[bytes]:
    """Interleave SSE keepalive markers between long gaps in upstream generator.

    Covers ALL phases: upstream loading, streaming, retry, post-processing.
    The keepalive is emitted whenever ``interval`` seconds pass without a chunk
    from the upstream generator.

    Unlike ``asyncio.wait_for(anext, timeout)``, this implementation does NOT
    cancel the pending ``__anext__()`` call when the timeout fires — otherwise
    the ``CancelledError`` propagates through the generator chain and kills the
    entire pipeline mid-stream (the "keepkilling" bug).

    If interval is 0 or None, keepalive is disabled (pass-through).
    """
    it = gen.__aiter__()
    effective_interval = interval if interval is not None else _SSE_KEEPALIVE_INTERVAL
    if effective_interval is None or effective_interval <= 0:
        # Keepalive disabled — forward chunks directly
        async for chunk in gen:
            yield chunk
        return

    # Reusable task that reads the next chunk from the generator.
    # We never cancel it — only check whether it completed.
    next_task = asyncio.ensure_future(it.__anext__())
    while True:
        sleep_task = asyncio.ensure_future(asyncio.sleep(effective_interval))
        try:
            done, _ = await asyncio.wait(
                [next_task, sleep_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if next_task in done:
                # A chunk arrived (or the generator finished)
                try:
                    chunk = next_task.result()
                except StopAsyncIteration:
                    return
                # Prime the next read before yielding (so the upstream
                # keeps flowing while we hand the chunk to the client)
                next_task = asyncio.ensure_future(it.__anext__())
                yield chunk
            else:
                # Timeout — no chunk yet; keep the reader alive
                log("DEBUG", "sse_keepalive")
                yield b": keepalive\n\n"
        finally:
            if not sleep_task.done():
                sleep_task.cancel()
                # Don't await here — the cancellation will be processed
                # on the next event loop cycle.  Awaiting it delays the
                # final StopAsyncIteration return by 1+ event loop ticks
                # (observed as ~5s lingering after downstream_complete).
                # The cancelled task is garbage-collected with the loop.


def _build_pipeline_if_configured(route):
    """Build V2 Pipeline from route filter_chain config."""
    if not FILTER_CHAIN_AVAILABLE:
        return None
    if not (route and hasattr(route, 'filter_chain') and route.filter_chain):
        return None
    api_key = getattr(route, 'api_key', None)
    p = Pipeline.from_route_config(route.filter_chain, api_key=api_key)
    log("DEBUG", "pipeline_build",
        route_name=route.name if hasattr(route, 'name') else '?',
        built=p is not None,
        filter_chain_keys=list(route.filter_chain.keys()) if route.filter_chain else [])
    return p


def _strip_last_image_url(messages: list) -> Optional[int]:
    """Remove the LAST image_url item from messages, replace with placeholder.

    Returns the number of image_url items stripped (0 or 1), or None if no
    image found.
    """
    for msg in reversed(messages):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for idx in range(len(content) - 1, -1, -1):
            item = content[idx]
            if isinstance(item, dict) and item.get("type") == "image_url":
                content[idx] = {
                    "type": "text",
                    "text": "[Image omitted due to error]",
                }
                return 1
    return 0


async def process_streaming_request(
    url: str,
    client: httpx.AsyncClient,
    payload: Dict[str, Any],
    route_headers: Dict[str, str],
    route: Route,
    req_id: str,
    request_timeout: float,
    fallback_attempts: List[Dict[str, str]],
    visited_models: Set[str],
    upstream_model: str,
    is_passthrough: bool,
    transform_reasoning_content: bool,
    add_empty_content_when_reasoning_only: bool,
    reasoning_placeholder: str,
    t_start: float,
    record_metrics_func: Any,
) -> AsyncIterator[bytes]:
    """Process a streaming chat completion request via filter chain.

    This is a thin driver that delegates all filtering logic to the configured
    filter chain. The handler itself contains no RLS/TLS/nudge/tool_rewrite logic.

    Yields:
        Streaming chunks (bytes), including final [DONE] marker
    """
    log("INFO", "stream_handler_entry",
        req_id=req_id, route_name=route.name if hasattr(route, 'name') else '?',
        has_fc=hasattr(route, 'filter_chain') and bool(route.filter_chain))
    t0 = time.perf_counter()
    pipeline = _build_pipeline_if_configured(route)
    log("INFO", "stream_handler_pipeline",
        req_id=req_id, pipeline_built=pipeline is not None)

    # Build upstream async generator
    async def upstream_stream(upstream_payload):
        log("DEBUG", "upstream_stream_connect",
            req_id=req_id, url=url[:80])
        chunk_count = 0
        close_reason = "unknown"
        try:
            async with client.stream("POST", url, json=upstream_payload, headers=route_headers) as resp:
                log("DEBUG", "upstream_stream_connected",
                    req_id=req_id, status=resp.status_code)
                async for chunk in resp.aiter_bytes():
                    chunk_count += 1
                    log("DEBUG", "upstream_stream_chunk",
                        req_id=req_id, count=chunk_count,
                        size=len(chunk), preview=chunk[:200].decode("utf-8", errors="replace"))
                    yield chunk
                close_reason = "normal"
        except GeneratorExit:
            close_reason = "generator_exit"
            raise
        except asyncio.CancelledError:
            close_reason = "cancelled"
            raise
        except Exception as e:
            close_reason = f"error:{type(e).__name__}"
            raise
        finally:
            log("INFO", "upstream_stream_closed",
                req_id=req_id, reason=close_reason, total_chunks=chunk_count)

    # Stream through pipeline or directly
    if pipeline:
        log("INFO", "pipeline_run_stream_start",
            req_id=req_id, route=route.name if hasattr(route, 'name') else '?')
        chunk_count = 0
        try:
            async for chunk in _interleave_keepalive(pipeline.run_stream(
                payload, req_id, upstream_model,
                route_name=getattr(route, 'name', ''),
                upstream_url=str(getattr(route, 'upstream_url', '')),
                upstream_stream=upstream_stream,
            )):
                chunk_count += 1
                if chunk_count <= 5 or chunk_count % 200 == 0:
                    log("DEBUG", "pipeline_run_stream_yield",
                        req_id=req_id, count=chunk_count,
                        size=len(chunk), preview=chunk[:80].decode("utf-8", errors="replace"))
                yield chunk
            log("INFO", "pipeline_run_stream_done",
                req_id=req_id, total_yielded=chunk_count)
        except GeneratorExit:
            log("INFO", "downstream_closed",
                req_id=req_id, reason="client_disconnect",
                chunks_yielded=chunk_count)
            return
        except Exception as e:
            log("ERROR", "streaming_error", req_id=req_id, error=str(e) or type(e).__name__)
            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n".encode("utf-8")
            # Yield finish_reason before [DONE] even on error
            stop_chunk = json.dumps({
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            })
            yield f"data: {stop_chunk}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
            return
        log("INFO", "downstream_complete",
            req_id=req_id, total_yielded=chunk_count)
        log("DEBUG", "stream_closed",
            req_id=req_id, chunks_yielded=chunk_count)
    else:
        # Fallback: direct upstream streaming (no filter chain)
        effective_payload = dict(payload)
        effective_payload.setdefault("stream_options", {})["include_usage"] = True
        try:
            async with client.stream("POST", url, json=effective_payload, headers=route_headers) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except Exception as e:
            log("ERROR", "streaming_error", req_id=req_id, error=str(e) or type(e).__name__)
            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n".encode("utf-8")
            # Yield finish_reason before [DONE] even on error
            stop_chunk = json.dumps({
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            })
            yield f"data: {stop_chunk}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
            return

    # Metrics calculation (simplified - most metrics now handled by filters)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    record_metrics_func({
        "model": upstream_model,
        "req_id": req_id,
        "stream": True,
        "elapsed_ms": elapsed_ms,
        "ttft_ms": None,
        "tps": None,
        "total_tps": None,
        "completion_tokens": None,
        "prompt_tokens": None,
        "total_tokens": None,
        "finish_reason": None,
        "passthrough": is_passthrough,
        "did_summarize": False,
        "completion_tokens_source": "missing",
    })




