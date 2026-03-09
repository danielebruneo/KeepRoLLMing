from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from copy import deepcopy
from typing import Any, AsyncIterator, Dict, List

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel


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

    def reset(self) -> None:
        self.scenario = deepcopy(DEFAULT_SCENARIO)
        self.calls_total = 0
        self.calls_by_kind.clear()
        self.calls_by_model.clear()
        self.requests.clear()

    def apply_scenario(self, data: Dict[str, Any]) -> None:
        self.scenario = _deep_merge(deepcopy(DEFAULT_SCENARIO), data)
        self.calls_total = 0
        self.calls_by_kind.clear()
        self.calls_by_model.clear()
        self.requests.clear()


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
    if "context compaction engine" in joined or "riassunto di contesto" in joined:
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


def _json_success(model: str, content: str, include_usage: bool) -> JSONResponse:
    payload: Dict[str, Any] = {
        "id": f"fake-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if include_usage:
        payload["usage"] = _usage_for(content)
    return JSONResponse(payload)


async def _stream_sse(
    model: str,
    pieces: List[str],
    *,
    include_usage: bool,
    ttft_ms: int,
    chunk_delay_ms: int,
    abort_after_chunks: int | None = None,
) -> AsyncIterator[bytes]:
    if ttft_ms > 0:
        await asyncio.sleep(ttft_ms / 1000.0)
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
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
        if abort_after_chunks is not None and idx >= abort_after_chunks:
            raise RuntimeError("simulated upstream stream abort")
        if chunk_delay_ms > 0:
            await asyncio.sleep(chunk_delay_ms / 1000.0)

    final_evt: Dict[str, Any] = {
        "id": f"fake-{int(time.time() * 1000)}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    content = "".join(pieces)
    if include_usage:
        final_evt["usage"] = _usage_for(content)
    yield f"data: {json.dumps(final_evt, ensure_ascii=False)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get("/__health")
    async def health() -> Dict[str, str]:
        return {"ok": "true"}

    @app.post("/__reset")
    async def reset() -> Dict[str, str]:
        STATE.reset()
        return {"ok": "true"}

    @app.post("/__scenario")
    async def set_scenario(payload: ScenarioPayload) -> Dict[str, str]:
        STATE.apply_scenario(payload.scenario)
        return {"ok": "true"}

    @app.get("/__stats")
    async def stats() -> Dict[str, Any]:
        return {
            "calls_total": STATE.calls_total,
            "calls_by_kind": dict(STATE.calls_by_kind),
            "calls_by_model": dict(STATE.calls_by_model),
            "requests": STATE.requests,
        }

    @app.get("/v0/models")
    async def list_models() -> Dict[str, Any]:
        data = []
        for model_id, cfg in (STATE.scenario.get("models") or {}).items():
            data.append({"id": model_id, "context_length": int(cfg.get("context_length", 4096))})
        return {"data": data}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
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

        content = str(action.get("content") or section.get("content") or f"{kind} ok")
        include_usage = bool(action.get("include_usage", section.get("include_usage", True)))

        if stream:
            pieces = action.get("stream_pieces") or section.get("stream_pieces") or [content]
            pieces = [str(p) for p in pieces]
            ttft_ms = int(action.get("ttft_ms", section.get("ttft_ms", 0)))
            chunk_delay_ms = int(action.get("chunk_delay_ms", section.get("chunk_delay_ms", 0)))
            abort_after_chunks = action.get("abort_after_chunks", section.get("abort_after_chunks"))
            return StreamingResponse(
                _stream_sse(
                    model,
                    pieces,
                    include_usage=include_usage,
                    ttft_ms=ttft_ms,
                    chunk_delay_ms=chunk_delay_ms,
                    abort_after_chunks=int(abort_after_chunks) if isinstance(abort_after_chunks, int) else None,
                ),
                media_type="text/event-stream",
            )

        return _json_success(model, content, include_usage=include_usage)

    return app
