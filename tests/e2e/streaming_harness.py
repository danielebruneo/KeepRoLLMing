"""Shared direct-handler harness for focused streaming E2E tests.

These tests deliberately exercise ``process_streaming_request`` without the
full subprocess server.  Keeping this adapter in one place makes that seam
explicit while server-level tests continue to use ``e2e.conftest``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx


def configure_scenario(fake_backend_url: str, scenario: dict[str, Any]) -> None:
    """Install a deterministic scenario in the fake upstream."""
    httpx.post(
        f"{fake_backend_url}/__scenario",
        json={"scenario": scenario},
        timeout=5,
    ).raise_for_status()


async def collect_streaming_chunks(
    route: Any,
    fake_backend_url: str,
    payload: dict[str, Any],
    *,
    req_id: str = "direct-stream-e2e",
) -> list[dict[str, Any]]:
    """Run the direct streaming handler and decode downstream SSE JSON."""
    from keeprollming.endpoints.streaming_handlers import process_streaming_request

    chunks: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        async for raw_chunk in process_streaming_request(
            url=f"{fake_backend_url}/v1/chat/completions",
            client=client,
            payload=dict(payload),
            route_headers={},
            route=route,
            req_id=req_id,
            request_timeout=15.0,
            fallback_attempts=[],
            visited_models=set(),
            upstream_model="test-model",
            is_passthrough=True,
            transform_reasoning_content=False,
            add_empty_content_when_reasoning_only=False,
            reasoning_placeholder="",
            t_start=time.perf_counter(),
            record_metrics_func=lambda _: None,
        ):
            for line in raw_chunk.decode("utf-8", errors="replace").splitlines():
                if not line.startswith("data:") or "[DONE]" in line:
                    continue
                try:
                    chunks.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    continue
    return chunks
