"""
E2E test: ToolRewriteFilter — rileva XML pseudo-tool-call e li converte in strutturati.

Non-streaming + streaming.
"""

import asyncio
import json
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List

import httpx
import pytest
import uvicorn


TR_PORT = 19994

_XML_CONTENT = """Let me check the server first.

<tool_call>
<function=run_shell_command>
<parameter=command>ls -la /tmp</parameter>
<parameter=description>Check temp files</parameter>
</function>
</tool_call>

Done!"""


@dataclass
class _Route:
    name: str = "test/tool_rewrite"
    pattern: str = "test/tool_rewrite/*"
    summary_enabled: bool = False
    passthrough_enabled: bool = True
    model: str | None = "test-model"
    upstream_url: str | None = None
    upstream_headers: dict = field(default_factory=dict)
    tool_rewrite_enabled: bool = False  # Legacy flag — should NOT be used
    tool_rewrite_patterns: list = field(default_factory=list)
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""
    filter_chain: dict | None = None
    request_timeout: float = 30.0
    fallback_chain: list = field(default_factory=list)
    model_pattern: str | None = None
    cost_priority: int = 999


@pytest.fixture(scope="module")
def fake_backend():
    from tests.e2e.fake_backend import create_app
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=TR_PORT, log_level="error")
    server = uvicorn.Server(config)
    def run():
        asyncio.run(server.serve())
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(2)
    yield f"http://127.0.0.1:{TR_PORT}"
    server.should_exit = True


def _step_scenario(fake_backend: str, scenario: dict):
    httpx.post(f"{fake_backend}/__scenario", json={"scenario": scenario}, timeout=5)


def _route() -> _Route:
    return _Route(filter_chain={
        "order": ["tool_rewrite"],
        "filters": {
            "tool_rewrite": {"enabled": True},
        },
    })


# ── Non-streaming tests ──────────────────────────────────────────────

class _MockResponse:
    def __init__(self, content="", model="test-model", finish_reason="stop",
                 tool_calls=None, usage=None):
        self.content = content or ""
        self.model = model
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls or []
        self.usage = usage


class TestToolRewriteNonStreaming:

    def test_rewrites_xml_to_tool_calls(self, fake_backend):
        """XML tool call in content → riscritto come tool_calls strutturati."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.orchestrator.filters.tool_rewrite_filter import ToolRewriteFilter

        f = ToolRewriteFilter({"enabled": True})
        chain = FilterChain(filters=[f], execution_order=["tool_rewrite"])
        ctx = FilterExecutionContext(req_id="tr_test")

        resp = _MockResponse(content=_XML_CONTENT)
        result = asyncio.run(chain.process_response(resp, ctx))

        # XML should be gone from content
        assert "<tool_call>" not in result.content
        assert "<function=" not in result.content
        # Should have structured tool_calls
        assert result.tool_calls
        assert result.tool_calls[0]["function"]["name"] == "run_shell_command"
        args = json.loads(result.tool_calls[0]["function"]["arguments"])
        assert "ls -la /tmp" in args.get("command", "")

    def test_passthrough_non_xml_content(self, fake_backend):
        """Content senza XML → passa inalterato."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.orchestrator.filters.tool_rewrite_filter import ToolRewriteFilter

        f = ToolRewriteFilter({"enabled": True})
        chain = FilterChain(filters=[f], execution_order=["tool_rewrite"])
        ctx = FilterExecutionContext(req_id="tr_test")

        original = "This is a normal response without any tool calls."
        resp = _MockResponse(content=original)
        result = asyncio.run(chain.process_response(resp, ctx))

        assert result.content == original
        assert not result.tool_calls


# ── Streaming tests ──────────────────────────────────────────────────

async def _collect_streaming_chunks(
    route: _Route, fake_backend_url: str, payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    from keeprollming.endpoints.streaming_handlers import process_streaming_request
    chunks = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        t_start = time.perf_counter()
        async for raw_chunk in process_streaming_request(
            url=f"{fake_backend_url}/v1/chat/completions",
            client=client, payload=dict(payload), route_headers={},
            route=route, req_id="tr-test", request_timeout=15.0,
            fallback_attempts=[], visited_models=set(),
            upstream_model="test-model", is_passthrough=True,
            transform_reasoning_content=False,
            add_empty_content_when_reasoning_only=False,
            reasoning_placeholder="", t_start=t_start,
            record_metrics_func=lambda _: None,
        ):
            text = raw_chunk.decode("utf-8", errors="replace")
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("data:") and "[DONE]" not in line:
                    try:
                        chunks.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        pass
    return chunks


class TestToolRewriteStreaming:

    def test_xml_buffered_then_rewritten(self, fake_backend):
        """XML tool call in streaming → bufferizzato, non yieldato, riscritto in Phase 3."""
        # Split XML into streaming pieces to simulate chunked delivery
        xml_parts = _XML_CONTENT.split("\n\n")
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [xml_parts],
                "include_usage": False,
            },
        })

        payload = {
            "model": "test-model", "stream": True,
            "messages": [{"role": "user", "content": "Check server"}],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route(), fake_backend, payload,
        ))

        # Collect all content deltas
        content_parts = []
        for c in chunks:
            delta = c.get("choices", [{}])[0].get("delta", {})
            if delta.get("content"):
                content_parts.append(delta["content"])

        full_content = "".join(content_parts)

        # XML should NOT appear in the streamed content
        assert "<tool_call>" not in full_content, \
            f"Raw XML leaked to client! Got: {full_content[:200]}"

    def test_non_xml_content_streamed_immediately(self, fake_backend):
        """Content senza XML → streamato subito, nessun buffer."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["Hello ", "world"]],
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model", "stream": True,
            "messages": [{"role": "user", "content": "Say hi"}],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route(), fake_backend, payload,
        ))
        content = "".join(
            c.get("choices", [{}])[0].get("delta", {}).get("content", "")
            for c in chunks
        )
        assert content == "Hello world"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
