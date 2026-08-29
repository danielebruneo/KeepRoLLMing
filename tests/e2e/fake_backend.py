from __future__ import annotations

import asyncio
import json
import random
import time
from collections import defaultdict
from copy import deepcopy
from typing import Any, AsyncIterator, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel


EMBEDDINGS_DIM = 256


class ControlPayload(BaseModel):
    action: str
    ms: int | None = None
    after_chunks: int | None = None


DEFAULT_SCENARIO: Dict[str, Any] = {
    "models": {
        "main-model": {"context_length": 4096},
        "summary-model": {"context_length": 2048},
        "live-model": {"context_length": 4096},
    },
    "chat": {
        "content": "FAKE BACKEND OK",
        "stream_pieces": ["FAKE ", "BACKEND ", "OK"],
        "include_usage": True,
        "ttft_ms": 0,
        "chunk_delay_ms": 0,
        "script": [],
    },
    "summary": {
        "content": "SUMMARY OK",
        "include_usage": True,
        "overflow_if_prompt_chars_gt": None,
        "overflow_message": "Prompt exceeds the available context size.",
        "script": [],
    },
}


class ScenarioPayload(BaseModel):
    scenario: Dict[str, Any]


class State:
    def __init__(self) -> None:
        self.scenario: Dict[str, Any] = deepcopy(DEFAULT_SCENARIO)
        self.calls_total = 0
        self.calls_by_kind: Dict[str, int] = defaultdict(int)
        self.calls_by_model: Dict[str, int] = defaultdict(int)
        self.requests: List[Dict[str, Any]] = []
        # /__control state
        self.delay_ms: int = 0
        self.refuse_next: bool = False
        self.stall_after_chunks: int | None = None
        # Degradation levels (ST-01)
        self.degradation_level: int = 0
        self.seed: int = 0
        self._prng_state: int = 0
        # Real HTTP lifecycle instrumentation.  These counters deliberately
        # live at the generator boundary, so an E2E client can prove that KRM
        # closed the upstream response after a downstream disconnect.
        self.active_streams: int = 0
        self.streams_started: int = 0
        self.streams_closed: int = 0

    def reset(self) -> None:
        self.scenario = deepcopy(DEFAULT_SCENARIO)
        self.calls_total = 0
        self.calls_by_kind.clear()
        self.calls_by_model.clear()
        self.requests.clear()
        self.delay_ms = 0
        self.refuse_next = False
        self.stall_after_chunks = None
        self.degradation_level = 0
        self.seed = 0
        self._prng_state = 0
        self.active_streams = 0
        self.streams_started = 0
        self.streams_closed = 0

    def apply_scenario(self, data: Dict[str, Any]) -> None:
        self.scenario = _deep_merge(deepcopy(DEFAULT_SCENARIO), data)
        # If script is set, clear the default content/stream_pieces
        # to avoid "FAKE BACKEND OK" bleeding through in streaming tests
        # But preserve array-format content/stream_pieces (for multi-retry testing)
        chat = self.scenario.get("chat", {})
        if chat.get("script"):
            # Only clear if not using array format
            content = chat.get("content")
            if not isinstance(content, list):
                chat.pop("content", None)
            stream_pieces = chat.get("stream_pieces")
            if not isinstance(stream_pieces, list) or (stream_pieces and not isinstance(stream_pieces[0], list)):
                chat.pop("stream_pieces", None)
        # Extract degradation_level and seed from scenario
        self.degradation_level = int(chat.get("degradation_level", 0))
        self.seed = int(chat.get("seed", 0))
        self._prng_state = self.seed
        self.calls_total = 0
        self.calls_by_kind.clear()
        self.calls_by_model.clear()
        self.requests.clear()

    def next_prng(self) -> float:
        """Deterministic PRNG based on LCG (Linear Congruential Generator).
        
        Returns a float in [0, 1) based on (seed, internal counter).
        Same seed + same sequence of calls = same sequence of values.
        """
        # LCG parameters (same as glibc)
        a = 1103515245
        c = 12345
        m = 2**31
        self._prng_state = (a * self._prng_state + c) % m
        return self._prng_state / m


STATE = State()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = deepcopy(v)
    return base


def _extract_text(messages: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for msg in messages or []:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    txt = item.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
    return "\n".join(parts)


def _kind_for_payload(model: str, messages: List[Dict[str, Any]]) -> str:
    if model == "summary-model":
        return "summary"
    joined = _extract_text(messages).lower()
    if "context compaction engine" in joined or "context summary" in joined:
        return "summary"
    return "chat"


def _next_action(kind: str) -> Dict[str, Any] | None:
    script = STATE.scenario.get(kind, {}).get("script") or []
    idx = STATE.calls_by_kind[kind] - 1
    if 0 <= idx < len(script):
        action = script[idx]
        if isinstance(action, dict):
            return action
    return None


def _usage_for(content: str) -> Dict[str, int]:
    completion = max(1, len(content.split()))
    prompt = max(1, completion * 2)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _json_success(model: str, content: str, include_usage: bool, tool_calls: list | None = None, reasoning_content: str | None = None) -> JSONResponse:
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    payload: Dict[str, Any] = {
        "id": f"fake-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
    }
    if include_usage:
        payload["usage"] = _usage_for(content)
    return JSONResponse(payload)


# ── Degradation Layer (ST-01) ────────────────────────────────────────────────

def _degrade_event(event: Dict[str, Any], level: int, prng_fn: callable, chunk_index: int) -> Dict[str, Any]:
    """Apply degradation transformations to a single SSE event.
    
    L1: Fragment tool_call arguments across multiple deltas
    L2: Interleave reasoning and content, add keepalive comments
    L3: Add empty deltas, mix line endings
    L4: Omit [DONE], duplicate events occasionally
    """
    if level == 0:
        return event
    
    # L1+ Fragment tool_call arguments
    if level >= 1:
        choices = event.get("choices", [])
        if choices and "delta" in choices[0]:
            delta = choices[0]["delta"]
            if "tool_calls" in delta:
                tc_list = delta["tool_calls"]
                if isinstance(tc_list, list):
                    for tc in tc_list:
                        if "function" in tc and "arguments" in tc["function"]:
                            args = tc["function"]["arguments"]
                            if isinstance(args, str) and len(args) > 20:
                                # Fragment arguments across multiple events
                                mid = len(args) // 2
                                tc["function"]["arguments"] = args[:mid]
                                # Note: caller must emit additional events for remaining args
    
    # L2+ Add keepalive comments (handled at chunk level, not event level)
    
    # L4+ Occasionally duplicate events
    if level >= 4 and prng_fn() < 0.1:
        # Signal that this event should be duplicated
        event["_duplicate"] = True
    
    return event


def _degrade_chunks(chunks: List[bytes], level: int, prng_fn: callable) -> List[bytes]:
    """Apply degradation transformations to SSE chunks.
    
    L2: Interleave reasoning and content boundaries
    L3: Split JSON across chunks, add empty chunks
    L4: Omit [DONE], split UTF-8 characters
    """
    if level == 0:
        return chunks
    
    result = []
    
    for i, chunk in enumerate(chunks):
        # L2+ Add keepalive comments occasionally
        if level >= 2 and prng_fn() < 0.1:
            result.append(b": ping\n\n")
        
        # L3+ Add empty/whitespace chunks occasionally
        if level >= 3 and prng_fn() < 0.05:
            result.append(b"data: {}\n\n")
        
        # L3+ Split JSON across chunks (simulate TCP fragmentation)
        if level >= 3 and len(chunk) > 100 and prng_fn() < 0.2:
            # Split the chunk in two
            mid = len(chunk) // 2
            result.append(chunk[:mid])
            result.append(chunk[mid:])
        else:
            result.append(chunk)
    
    # L4+ Omit [DONE] occasionally
    if level >= 4:
        # Remove last [DONE] marker if present
        if result and result[-1] == b"data: [DONE]\n\n":
            if prng_fn() < 0.5:
                result = result[:-1]
    
    return result


async def _stream_sse(
    model: str,
    pieces: List[str],
    *,
    include_usage: bool,
    ttft_ms: int,
    chunk_delay_ms: int,
    abort_after_chunks: int | None = None,
    tool_calls_delta: Dict | None = None,
    reasoning_pieces: List[str] | None = None,
    final_finish_reason: str | None = None,
) -> AsyncIterator[bytes]:
    STATE.active_streams += 1
    STATE.streams_started += 1
    try:
        if ttft_ms > 0:
            await asyncio.sleep(ttft_ms / 1000.0)

        level = STATE.degradation_level
        chunks: List[bytes] = []

        # Emit reasoning_content pieces first if present (for RLS streaming tests)
        if reasoning_pieces:
            for rp in reasoning_pieces:
                event = {
                    "id": f"fake-{int(time.time() * 1000)}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"reasoning_content": rp},
                        "finish_reason": None,
                    }],
                }
                chunks.append(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))

        # Emit tool_calls delta if present (for streaming TLS tests)
        # tool_calls_delta must be a full SSE chunk dict or a list of tool_calls
        if tool_calls_delta:
            if "choices" in tool_calls_delta and "delta" in tool_calls_delta.get("choices", [{}])[0]:
                tc_event = tool_calls_delta
            else:
                tc_event = {
                    "id": f"fake-{int(time.time() * 1000)}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"tool_calls": tool_calls_delta if isinstance(tool_calls_delta, list) else [tool_calls_delta]},
                        "finish_reason": None,
                    }],
                }
            chunks.append(f"data: {json.dumps(tc_event, ensure_ascii=False)}\n\n".encode("utf-8"))

        for idx, piece in enumerate(pieces, start=1):
            event = {
                "id": f"fake-{int(time.time() * 1000)}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": piece},
                        "finish_reason": None,
                    }
                ],
            }
            chunks.append(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
            if abort_after_chunks is not None and idx >= abort_after_chunks:
                raise RuntimeError("simulated upstream stream abort")

        final_evt: Dict[str, Any] = {
            "id": f"fake-{int(time.time() * 1000)}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": final_finish_reason or ("tool_calls" if tool_calls_delta else "stop")}],
        }
        content = "".join(pieces)
        if include_usage:
            final_evt["usage"] = _usage_for(content)
        chunks.append(f"data: {json.dumps(final_evt, ensure_ascii=False)}\n\n".encode("utf-8"))
        chunks.append(b"data: [DONE]\n\n")

        # Apply degradation layer
        degraded_chunks = _degrade_chunks(chunks, level, STATE.next_prng)

        # The delay belongs between bytes yielded to the peer, not while this
        # generator is building its list.  Sleeping in the construction loop
        # made every test response appear as a single delayed burst and hid
        # precisely the downstream-cadence regressions this backend exists to
        # exercise.
        for index, chunk in enumerate(degraded_chunks):
            yield chunk
            if chunk_delay_ms > 0 and index < len(degraded_chunks) - 1:
                await asyncio.sleep(chunk_delay_ms / 1000.0)
    finally:
        STATE.active_streams -= 1
        STATE.streams_closed += 1


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/__health")
    async def health() -> Dict[str, str]:
        return {"ok": "true"}

    @app.post("/__reset")
    async def reset() -> Dict[str, str]:
        STATE.reset()
        return {"ok": "true"}

    @app.post("/__control")
    async def control(payload: ControlPayload) -> Dict[str, Any]:
        """Control endpoint for error simulation.

        Actions:
        - delay ms=N:  delay next request by N ms (simulates slow upstream)
        - refuse:      refuse next request (simulates connection error)
        - stall after=N: stop yielding after N SSE chunks (simulates dead stream)
        - reset:       clear all control flags
        """
        act = payload.action
        if act == "delay":
            STATE.delay_ms = payload.ms or 5000
            return {"ok": "true", "action": "delay", "ms": STATE.delay_ms}
        elif act == "refuse":
            STATE.refuse_next = True
            return {"ok": "true", "action": "refuse"}
        elif act == "stall":
            STATE.stall_after_chunks = payload.after_chunks or 1
            return {"ok": "true", "action": "stall", "after_chunks": STATE.stall_after_chunks}
        elif act == "reset":
            STATE.delay_ms = 0
            STATE.refuse_next = False
            STATE.stall_after_chunks = None
            return {"ok": "true", "action": "reset"}
        return JSONResponse({"error": f"unknown action: {act}"}, status_code=400)

    @app.post("/__scenario")
    async def set_scenario(payload: ScenarioPayload) -> Dict[str, str]:
        STATE.apply_scenario(payload.scenario)
        return {"ok": "true"}

    @app.post("/__degrade")
    async def set_degradation(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Set degradation level and seed for realistic stream testing.
        
        Args:
            level: Degradation level (0-4)
            seed: Random seed for deterministic output
        
        Returns:
            Current degradation settings
        """
        level = int(payload.get("level", 0))
        seed = int(payload.get("seed", 0))
        
        if level < 0 or level > 4:
            return JSONResponse(
                {"error": "level must be 0-4"},
                status_code=400
            )
        
        STATE.degradation_level = level
        STATE.seed = seed
        STATE._prng_state = seed
        
        return {
            "ok": "true",
            "level": STATE.degradation_level,
            "seed": STATE.seed
        }

    @app.get("/__stats")
    async def stats() -> Dict[str, Any]:
        return {
            "calls_total": STATE.calls_total,
            "calls_by_kind": dict(STATE.calls_by_kind),
            "calls_by_model": dict(STATE.calls_by_model),
            "requests": STATE.requests,
            "active_streams": STATE.active_streams,
            "streams_started": STATE.streams_started,
            "streams_closed": STATE.streams_closed,
        }

    @app.get("/v0/models")
    async def list_models() -> Dict[str, Any]:
        data = []
        for model_id, cfg in (STATE.scenario.get("models") or {}).items():
            data.append({"id": model_id, "context_length": int(cfg.get("context_length", 4096))})
        return {"data": data}

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        payload = await request.json()
        model = str(payload.get("model") or "unknown")
        inputs = payload.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]

        STATE.calls_total += 1
        STATE.calls_by_kind["embedding"] += 1
        STATE.calls_by_model[model] += 1

        text = _extract_text([{"content": " ".join(inputs)}])
        STATE.requests.append({
            "kind": "embedding",
            "model": model,
            "messages_count": len(inputs),
            "prompt_chars": len(text),
        })

        data = []
        for i, inp in enumerate(inputs):
            data.append({
                "index": i,
                "embedding": [0.1 * (j % 10) for j in range(EMBEDDINGS_DIM)],
                "object": "embedding",
            })

        return JSONResponse({
            "object": "list",
            "data": data,
            "model": model,
            "usage": {"prompt_tokens": max(1, len(text) // 4), "total_tokens": max(1, len(text) // 4)},
        })

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        # ── Apply /__control actions before processing ──────────────
        if STATE.refuse_next:
            STATE.refuse_next = False
            raise RuntimeError("refused by /__control")

        if STATE.delay_ms > 0:
            d = STATE.delay_ms
            STATE.delay_ms = 0
            await asyncio.sleep(d / 1000.0)

        payload = await request.json()
        model = str(payload.get("model") or "unknown")
        messages = payload.get("messages") or []
        stream = bool(payload.get("stream", False))
        kind = _kind_for_payload(model, messages)

        STATE.calls_total += 1
        STATE.calls_by_kind[kind] += 1
        STATE.calls_by_model[model] += 1

        text = _extract_text(messages)
        STATE.requests.append(
            {
                "kind": kind,
                "model": model,
                "stream": stream,
                "messages_count": len(messages),
                "prompt_chars": len(text),
            }
        )

        section = STATE.scenario.get(kind, {})
        action = _next_action(kind) or {}
        if action.get("type") == "error":
            status = int(action.get("status", 500))
            message = str(action.get("message") or "forced backend error")
            return JSONResponse({"error": {"message": message}}, status_code=status)

        overflow_limit = action.get("overflow_if_prompt_chars_gt", section.get("overflow_if_prompt_chars_gt"))
        if isinstance(overflow_limit, int) and len(text) > overflow_limit:
            message = str(action.get("overflow_message") or section.get("overflow_message") or "Prompt exceeds the available context size.")
            return JSONResponse({"error": {"message": message}}, status_code=400)

        # Support array of responses for multi-retry testing
        content_or_array = action.get("content", section.get("content"))
        if isinstance(content_or_array, list):
            # Return responses in sequence based on call index
            content_idx = STATE.calls_by_kind[kind] - 1
            if 0 <= content_idx < len(content_or_array):
                raw_content = content_or_array[content_idx]
                content = str(raw_content) if raw_content is not None else ""
            else:
                raw_content = content_or_array[-1]
                content = str(raw_content) if raw_content is not None else ""
        else:
            content = str(content_or_array or f"{kind} ok")

        include_usage = bool(action.get("include_usage", section.get("include_usage", True)))
        tool_calls = action.get("tool_calls") or section.get("tool_calls")
        # Support array of tool_calls for multi-retry testing (indexed by call number)
        # Format: {"indexed": [[tc1_dict], None, [tc2_dict]]}
        #   each element: list of tool_call dicts, or None for no tool_calls
        if isinstance(tool_calls, dict) and "indexed" in tool_calls:
            tc_arr = tool_calls["indexed"]
            tc_idx = STATE.calls_by_kind[kind] - 1
            if 0 <= tc_idx < len(tc_arr):
                tool_calls = tc_arr[tc_idx] or None

        if stream:
            pieces = action.get("stream_pieces") or section.get("stream_pieces") or [content]
            
            # Support array of response arrays for multi-retry testing in streaming mode
            if isinstance(pieces, list) and pieces and isinstance(pieces[0], list):
                # Nested array: return appropriate set based on call index
                pieces_idx = STATE.calls_by_kind[kind] - 1
                if 0 <= pieces_idx < len(pieces):
                    pieces = [str(p) if p is not None else "" for p in pieces[pieces_idx]]
                else:
                    pieces = [str(p) if p is not None else "" for p in pieces[-1]]  # Fallback to last
            else:
                pieces = [str(p) if p is not None else "" for p in pieces]
            
            ttft_ms = int(action.get("ttft_ms", section.get("ttft_ms", 0)))
            chunk_delay_ms = int(action.get("chunk_delay_ms", section.get("chunk_delay_ms", 0)))
            abort_after_chunks = action.get("abort_after_chunks", section.get("abort_after_chunks"))
            if STATE.stall_after_chunks is not None:
                abort_after_chunks = STATE.stall_after_chunks
                STATE.stall_after_chunks = None
            return StreamingResponse(
                _stream_sse(
                    model,
                    pieces,
                    include_usage=include_usage,
                    ttft_ms=ttft_ms,
                    chunk_delay_ms=chunk_delay_ms,
                    abort_after_chunks=int(abort_after_chunks) if isinstance(abort_after_chunks, int) else None,
                    tool_calls_delta=tool_calls,
                    reasoning_pieces=action.get("reasoning_pieces", section.get("reasoning_pieces")),
                    final_finish_reason=action.get("final_finish_reason", section.get("final_finish_reason")),
                ),
                media_type="text/event-stream",
            )

        reasoning_content = action.get("reasoning_content", section.get("reasoning_content"))
        return _json_success(model, content, include_usage=include_usage, tool_calls=tool_calls,
                             reasoning_content=reasoning_content)

    return app
