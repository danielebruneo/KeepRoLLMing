"""
E2E test: ToolRewriteFilter — rileva XML pseudo-tool-call e li converte in strutturati.

Non-streaming + streaming.
"""

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from tests.e2e.conftest import fake_backend_server  # noqa: F401

from tests.e2e.streaming_harness import (
    collect_streaming_chunks as _collect_streaming_chunks,
    configure_scenario as _step_scenario,
)


_fake_backend_url: str | None = None

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
    filters: dict | None = None
    request_timeout: float = 30.0
    fallback_chain: list = field(default_factory=list)
    model_pattern: str | None = None
    cost_priority: int = 999


@pytest.fixture
def fake_backend(fake_backend_server):
    """Use the dynamically allocated canonical fake backend."""
    global _fake_backend_url
    _fake_backend_url = fake_backend_server.base_url
    return _fake_backend_url


def _route() -> _Route:
    assert _fake_backend_url is not None
    return _Route(
        filters={"tool_rewrite": {"enabled": True}},
        upstream_url=_fake_backend_url,
    )


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
        from keeprollming.filters.tool_rewrite.request import ToolRewriteFilter

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
        from keeprollming.filters.tool_rewrite.request import ToolRewriteFilter

        f = ToolRewriteFilter({"enabled": True})
        chain = FilterChain(filters=[f], execution_order=["tool_rewrite"])
        ctx = FilterExecutionContext(req_id="tr_test")

        original = "This is a normal response without any tool calls."
        resp = _MockResponse(content=original)
        result = asyncio.run(chain.process_response(resp, ctx))

        assert result.content == original
        assert not result.tool_calls


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
