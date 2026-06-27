"""
E2E test: RLS streaming buffer — blocca reasoning in loop PRIMA del client.

Config produzione-like.
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


RLS_PORT = 19995

_SAME_REASONING = "Let me search for the footer file to fix the copyright..."
_DIFF_REASONING = "Now I need to sync the file to the server and flush cache."
_READFILE_TC = [{
    "index": 0,
    "id": "call_read",
    "type": "function",
    "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'},
}]


@dataclass
class _Route:
    name: str = "test/rls"
    pattern: str = "test/rls/*"
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
    filter_chain: dict | None = None
    request_timeout: float = 30.0
    fallback_chain: list = field(default_factory=list)
    model_pattern: str | None = None
    cost_priority: int = 999


@pytest.fixture(scope="module")
def fake_backend():
    from tests.e2e.fake_backend import create_app
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=RLS_PORT, log_level="error")
    server = uvicorn.Server(config)
    def run():
        asyncio.run(server.serve())
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(2)
    yield f"http://127.0.0.1:{RLS_PORT}"
    server.should_exit = True


def _step_scenario(fake_backend: str, scenario: dict):
    httpx.post(f"{fake_backend}/__scenario", json={"scenario": scenario}, timeout=5)


def _route(override: dict | None = None) -> _Route:
    cfg = {
        "enabled": True,
        "max_repeats": 1,
        "max_retries": 2,
        **(override or {}),
        "upstream_url": f"http://127.0.0.1:{RLS_PORT}",
    }
    return _Route(filter_chain={
        "order": ["reasoning_loop_stopper"],
        "filters": {"reasoning_loop_stopper": cfg},
    })


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
            route=route, req_id="rls-test", request_timeout=15.0,
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


class TestRLSStreaming:

    def test_same_reasoning_blocked(self, fake_backend):
        """Stesso reasoning del turno precedente → buffer scartato, fallback."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [[]],
                "reasoning_pieces": [_SAME_REASONING],
                "content": "Should not appear (RLS loop detected)",
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model", "stream": True,
            "messages": [
                {"role": "user", "content": "Fix footer"},
                {"role": "assistant", "content": None,
                 "reasoning_content": _SAME_REASONING,
                 "tool_calls": [{"id": "prev", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"footer"}'}}]},
                {"role": "tool", "tool_call_id": "prev", "content": "result"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_retries": 0}), fake_backend, payload,
        ))
        reasoning_seen = any(
            c.get("choices", [{}])[0].get("delta", {}).get("reasoning_content")
            for c in chunks
        )
        assert not reasoning_seen, "Loop reasoning was forwarded to client!"

    def test_different_reasoning_flushed(self, fake_backend):
        """Reasoning diverso dal precedente → buffer flushato al client."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [[]],
                "reasoning_pieces": [_DIFF_REASONING],
                "content": "Different approach result",
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model", "stream": True,
            "messages": [
                {"role": "user", "content": "Do something"},
                {"role": "assistant", "content": None,
                 "reasoning_content": _SAME_REASONING,
                 "tool_calls": [{"id": "prev", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"x"}'}}]},
                {"role": "tool", "tool_call_id": "prev", "content": "result"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_retries": 0}), fake_backend, payload,
        ))
        reasoning_parts = []
        for c in chunks:
            delta = c.get("choices", [{}])[0].get("delta", {})
            rc = delta.get("reasoning_content")
            if rc:
                reasoning_parts.append(rc)
        full = "".join(reasoning_parts)
        assert _DIFF_REASONING in full, \
            f"Different reasoning should reach client, got: {full[:100]}"

    def test_max_retries_zero_immediate_fallback(self, fake_backend):
        """max_retries=0 → fallback subito, nessun reasoning al client."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [[]],
                "reasoning_pieces": [_SAME_REASONING],
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model", "stream": True,
            "messages": [
                {"role": "user", "content": "Fix footer"},
                {"role": "assistant", "content": None,
                 "reasoning_content": _SAME_REASONING,
                 "tool_calls": [{"id": "prev", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"footer"}'}}]},
                {"role": "tool", "tool_call_id": "prev", "content": "result"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_retries": 0}), fake_backend, payload,
        ))
        reasoning_seen = any(
            c.get("choices", [{}])[0].get("delta", {}).get("reasoning_content")
            for c in chunks
        )
        assert not reasoning_seen, "Reasoning in loop with max_retries=0!"

    def test_no_previous_reasoning_passes(self, fake_backend):
        """Nessun reasoning precedente → reasoning arriva al client."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["Hello ", "world"]],
                "reasoning_pieces": [_DIFF_REASONING],
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model", "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route(), fake_backend, payload,
        ))
        reasoning_parts = []
        content_parts = []
        for c in chunks:
            delta = c.get("choices", [{}])[0].get("delta", {})
            rc = delta.get("reasoning_content")
            if rc:
                reasoning_parts.append(rc)
            cc = delta.get("content")
            if cc:
                content_parts.append(cc)
        full_r = "".join(reasoning_parts)
        full_c = "".join(content_parts)
        assert _DIFF_REASONING in full_r, \
            f"First-time reasoning should reach client, got: {full_r[:100]}"
        assert "Hello" in full_c, "Content should also reach client"

    def test_reasoning_passes_with_whitespace_before_content(self, fake_backend):
        """Spazi/formatting in content PRIMA del testo reale non fermano il buffer."""
        # reasoning_pieces manda chunk con reasoning_content
        # stream_pieces con [''] simula chunk vuoto (spazio) dopo reasoning
        # Niente tool_calls — RLS deve fermarsi solo su content SENZA reasoning
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["", "Ecco i risultati."]],
                "reasoning_pieces": [_DIFF_REASONING],
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model", "stream": True,
            "messages": [
                {"role": "user", "content": "Cerca"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_retries": 0}), fake_backend, payload,
        ))
        reasoning_parts = []
        for c in chunks:
            delta = c.get("choices", [{}])[0].get("delta", {})
            rc = delta.get("reasoning_content")
            if rc:
                reasoning_parts.append(rc)
        full = "".join(reasoning_parts)
        assert _DIFF_REASONING in full, \
            f"Reasoning should reach client, got: {full[:100]}"

    def test_content_without_reasoning_stops_buffer(self, fake_backend):
        """Stesso reasoning ma tool call DIVERSA → NON e' un loop."""
        # Simula: model fa reasoning, poi content senza reasoning
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["Risultato finale."]],
                "reasoning_pieces": [_SAME_REASONING],
                "tool_calls": _READFILE_TC,
                "include_usage": False,
            },
        })
        # Stesso reasoning del turno precedente MA tool call diversa → non loop
        payload = {
            "model": "test-model", "stream": True,
            "messages": [
                {"role": "user", "content": "Cerca"},
                {"role": "assistant", "content": None,
                 "reasoning_content": _SAME_REASONING,
                 "tool_calls": [{"id": "prev", "type": "function",
                                 "function": {"name": "search", "arguments": '{"q":"x"}'}}]},
                {"role": "tool", "tool_call_id": "prev", "content": "risultato"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_retries": 0}), fake_backend, payload,
        ))
        # Reasoning DEVE arrivare al client (tool call diversa = non loop)
        reasoning_seen = any(
            c.get("choices", [{}])[0].get("delta", {}).get("reasoning_content")
            for c in chunks
        )
        assert reasoning_seen, \
            "Reasoning with different tool calls should reach client (not a loop)"

    def test_same_reasoning_same_tool_call_is_loop(self, fake_backend):
        """Stesso reasoning E stessa tool call → loop (da bloccare)."""
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["Risultato finale."]],
                "reasoning_pieces": [_SAME_REASONING],
                "tool_calls": _READFILE_TC,
                "include_usage": False,
            },
        })
        # Stesso reasoning E stessa tool call del turno precedente → loop
        payload = {
            "model": "test-model", "stream": True,
            "messages": [
                {"role": "user", "content": "Leggi"},
                {"role": "assistant", "content": None,
                 "reasoning_content": _SAME_REASONING,
                 "tool_calls": _READFILE_TC},
                {"role": "tool", "tool_call_id": "read0", "content": "risultato"},
            ],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route({"max_retries": 0}), fake_backend, payload,
        ))
        # Reasoning NON deve arrivare al client (loop con stessa tool call)
        reasoning_seen = any(
            c.get("choices", [{}])[0].get("delta", {}).get("reasoning_content")
            for c in chunks
        )
        assert not reasoning_seen, \
            "Loop reasoning with same tool call should NOT reach client"


    def test_safety_flush_on_non_terminal_finish_reason(self, fake_backend):
        """Stream finisce con finish_reason != stop/tool_calls (es. 'length') mentre
        il buffer RLS e' ancora aperto → safety flush: il reasoning bufferizzato DEVE
        arrivare al client, non essere perso.

        Regressione: prima del safety flush, un reasoning bufferizzato che non veniva
        chiuso da uno stop terminale veniva scartato silenziosamente (risposta vuota).
        Nessun reasoning precedente in cronologia → non e' un loop, deve passare.
        """
        _step_scenario(fake_backend, {
            "chat": {
                "stream_pieces": [["Risultato ", "parziale"]],
                "reasoning_pieces": [_DIFF_REASONING],
                "final_finish_reason": "length",  # NON 'stop' → buffer resta aperto
                "include_usage": False,
            },
        })
        payload = {
            "model": "test-model", "stream": True,
            "messages": [{"role": "user", "content": "Fai qualcosa"}],
        }
        chunks = asyncio.run(_collect_streaming_chunks(
            _route(), fake_backend, payload,
        ))
        reasoning_parts = []
        content_parts = []
        for c in chunks:
            delta = c.get("choices", [{}])[0].get("delta", {})
            if delta.get("reasoning_content"):
                reasoning_parts.append(delta["reasoning_content"])
            if delta.get("content"):
                content_parts.append(delta["content"])
        full_r = "".join(reasoning_parts)
        full_c = "".join(content_parts)
        assert _DIFF_REASONING in full_r, \
            f"Buffered reasoning lost on non-terminal finish_reason! Got: {full_r[:100]}"
        assert "Risultato" in full_c, \
            f"Buffered content lost on non-terminal finish_reason! Got: {full_c[:100]}"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
