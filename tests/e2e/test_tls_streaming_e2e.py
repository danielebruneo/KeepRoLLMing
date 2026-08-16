"""
E2E test: TLS streaming buffer — blocca tool_call in loop PRIMA del client.

Configurazione realistica (produzione):
  - max_repeats: 1, fuzzy_max_repeats: 2, fuzzy_look_back: 4
  - ab_loop_detection: true, send_user_message: true
  - max_attempts: 3, tls_message personalizzato, fallback personalizzati
"""

import asyncio
from dataclasses import dataclass, field

import pytest

from tests.e2e.streaming_harness import (
    collect_streaming_chunks as _collect_streaming_chunks,
    configure_scenario as _step_scenario,
)


_fake_backend_url: str | None = None

# ── Produzione-like TLS config ───────────────────────────────────────

_PROD_TLS_CONFIG = {
    "enabled": True,
    "max_repeats": 1,
    "max_attempts": 3,
    "fuzzy_max_repeats": 2,
    "fuzzy_look_back": 4,
    "ab_loop_detection": True,
    "send_user_message": True,
    "fallback_template": (
        "The system stopped repeated call: {name}({args}). "
        "Please try a different approach."
    ),
    "tls_message": (
        "You've already executed this tool call with the same arguments multiple times. "
        "Please proceed with the next step or try a different approach or report to the user. "
        "I shall break this loop!"
    ),
    "fallback_message": (
        "I notice I'm repeating the same tool call. I certainly don't want to loop. "
        "Let me try something different now or maybe I will just report to user..."
    ),
    "fallback_streaming_message": (
        "There's a loop on my mind but I won't let it win, I'll rather pause and reconsider."
    ),
}

_PROD_TLS_FILTERS = {
    "model_tool_loop_stopper": {
        **_PROD_TLS_CONFIG,
        "upstream_url": None,
    },
}


# ── Minimal route ───────────────────────────────────────────────────

@dataclass
class _Route:
    name: str = "test/tls"
    pattern: str = "test/tls/*"
    summary_enabled: bool = False
    passthrough_enabled: bool = True
    model: str | None = "test-model"
    upstream_url: str | None = None
    upstream_headers: dict = field(default_factory=dict)
    tool_rewrite_enabled: bool = False
    tool_rewrite_patterns: list = field(default_factory=list)
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""
    filters: dict | None = None
    request_timeout: float = 30.0
    fallback_chain: list = field(default_factory=list)
    model_pattern: str | None = None
    cost_priority: int = 999


# ── Fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def fake_backend(fake_backend_server):
    """Use the dynamically allocated canonical fake backend."""
    global _fake_backend_url
    _fake_backend_url = fake_backend_server.base_url
    return _fake_backend_url


def _route(tls_config_override: dict | None = None) -> _Route:
    """Build route with production TLS config, optionally overridden."""
    assert _fake_backend_url is not None
    filters = {
        "model_tool_loop_stopper": {
            **_PROD_TLS_CONFIG,
            **(tls_config_override or {}),
            "upstream_url": _fake_backend_url,
        },
    }
    return _Route(filters=filters, upstream_url=_fake_backend_url)


# ── Constants ────────────────────────────────────────────────────────

_SEARCH_TC = [{
    "index": 0,
    "id": "call_search",
    "type": "function",
    "function": {"name": "search", "arguments": '{"q":"langhe"}'},
}]

_READFILE_TC = [{
    "index": 0,
    "id": "call_read",
    "type": "function",
    "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'},
}]

_SHELL_TC = [{
    "index": 0,
    "id": "call_shell",
    "type": "function",
    "function": {"name": "run_shell_command", "arguments": '{"command":"ls"}'},
}]


# ── Tests ────────────────────────────────────────────────────────────

class TestTLSStreamingBuffer:

    def test_non_loop_tool_call_passes_through(self, fake_backend):
        """Tool call NOT in history → chunks pass through unmodified."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["Hello ", "world"]],
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        chunks = asyncio.run(_collect_streaming_chunks(_route(), fake_backend, payload))
        content_parts = []
        for c in chunks:
            delta = c.get("choices", [{}])[0].get("delta", {})
            if delta.get("content"):
                content_parts.append(delta["content"])
            if delta.get("tool_calls"):
                pytest.fail(f"Unexpected tool_call: {delta['tool_calls']}")
        assert "".join(content_parts) == "Hello world"

    def test_loop_blocks_tool_call_and_sends_fallback(self, fake_backend):
        """Repeated tool_call → buffer scartato, fallback inviato."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [[]],
                "tool_calls": _READFILE_TC,
                "content": "Should not appear (max_attempts exhausted)",
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Read file"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "prev", "type": "function",
                                 "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}}]},
                {"role": "tool", "tool_call_id": "prev", "content": "content here"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_attempts": 1}),
            fake_backend, payload,
        ))
        tc_seen = False
        content_parts = []
        for c in chunks:
            delta = c.get("choices", [{}])[0].get("delta", {})
            if delta.get("tool_calls"):
                tc_seen = True
            if delta.get("content"):
                content_parts.append(delta["content"])
        assert not tc_seen, "Loop tool_call forwarded to client!"
        full = "".join(content_parts)
        assert full == _PROD_TLS_CONFIG["fallback_streaming_message"], \
            f"Expected configured TLS streaming fallback, got: {full[:200]}"

    def test_max_attempts_zero_immediate_fallback(self, fake_backend):
        """max_attempts=0 → fallback subito, nessun retry."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [[]],
                "tool_calls": _SEARCH_TC,
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Search"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "prev", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"langhe"}'}}]},
                {"role": "tool", "tool_call_id": "prev", "content": "result"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_attempts": 0}),
            fake_backend, payload,
        ))
        tc_seen = any(
            c.get("choices", [{}])[0].get("delta", {}).get("tool_calls")
            for c in chunks
        )
        assert not tc_seen, "Loop tool_call forwarded with max_attempts=0!"
        content = "".join(
            c.get("choices", [{}])[0].get("delta", {}).get("content", "")
            for c in chunks
        )
        assert content == _PROD_TLS_CONFIG["fallback_streaming_message"], \
            f"Expected configured TLS streaming fallback, got: {content[:200]}"

    def test_content_before_tool_call_is_yielded(self, fake_backend):
        """Content/reasoning chunks BEFORE tool_call sono yieldati subito."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["Let me ", "think..."]],
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        chunks = asyncio.run(_collect_streaming_chunks(_route(), fake_backend, payload))
        content = "".join(
            c.get("choices", [{}])[0].get("delta", {}).get("content", "")
            for c in chunks
        )
        assert "Let me" in content, f"Content prima del tool_call non arriva: {content[:100]}"

    def test_fuzzy_loop_detection(self, fake_backend):
        """Stessa funzione chiamata 2+ volte in 4 messaggi → fuzzy loop."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [[]],
                "tool_calls": _SEARCH_TC,
                "include_usage": False,
            },
        })
        # History: search(langhe) già chiamata 2 volte (fuzzy_max_repeats=2)
        payload = {
            "model": "test-model",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Search"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c1", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"langhe"}'}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "res1"},
                {"role": "user", "content": "Again"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "c2", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"langhe"}'}}]},
                {"role": "tool", "tool_call_id": "c2", "content": "res2"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"fuzzy_max_repeats": 2, "fuzzy_look_back": 4, "max_attempts": 0}),
            fake_backend, payload,
        ))
        tc_seen = any(
            c.get("choices", [{}])[0].get("delta", {}).get("tool_calls")
            for c in chunks
        )
        assert not tc_seen, "Fuzzy-loop tool_call forwarded!"

    def test_ab_loop_detection(self, fake_backend):
        """Pattern alternating A,B,A,B → AB loop detection."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [[]],
                "tool_calls": _SEARCH_TC,
                "include_usage": False,
            },
        })
        # History: search, read_file, search (3 chiamate) → current search fa A,B,A,B
        payload = {
            "model": "test-model",
            "stream": True,
            "messages": [
                {"role": "user", "content": "Do something"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "a1", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"langhe"}'}}]},
                {"role": "tool", "tool_call_id": "a1", "content": "r1"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "b1", "type": "function",
                                 "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}}]},
                {"role": "tool", "tool_call_id": "b1", "content": "r2"},
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "a2", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"langhe"}'}}]},
                {"role": "tool", "tool_call_id": "a2", "content": "r3"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"ab_loop_detection": True, "max_attempts": 0}),
            fake_backend, payload,
        ))
        tc_seen = any(
            c.get("choices", [{}])[0].get("delta", {}).get("tool_calls")
            for c in chunks
        )
        assert not tc_seen, "AB-loop tool_call forwarded!"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
