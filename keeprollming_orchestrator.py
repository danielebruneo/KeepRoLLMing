"""KeepRoLLMing - minimal OpenAI-compatible chat completions orchestrator

Goals (v0 - strip the superfluo):
- Only exposes: POST /v1/chat/completions
- Proxy to an upstream OpenAI-compatible backend (e.g. LM Studio)
- Retrieves (best-effort) effective context length from upstream model metadata (/v1/models)
- If the incoming conversation is too long for the model context:
    Keep: system prompt (if present) + first 2 msgs (head) + last 2 msgs (tail)
    Summarize: everything in the middle via a smaller model (non-streaming)
    Repack and send to upstream main model
- Prioritize clarity + simple logging (JSON lines)

Run:
  python keeprollming_orchestrator.py

Env:
  UPSTREAM_BASE_URL   default: http://127.0.0.1:1234/v1
  MAIN_MODEL          default: qwen2.5-3b-instruct
  SUMMARY_MODEL       default: qwen2.5-1.5b-instruct
  DEFAULT_CTX_LEN     default: 4096
  SUMMARY_MAX_TOKENS  default: 256
  SAFETY_MARGIN_TOK   default: 128
"""

from __future__ import annotations

import os
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

# ----------------------------
# Configuration
# ----------------------------

UPSTREAM_BASE_URL = os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
MAIN_MODEL = os.getenv("MAIN_MODEL", "qwen2.5-3b-instruct")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "qwen2.5-1.5b-instruct")

DEFAULT_CTX_LEN = int(os.getenv("DEFAULT_CTX_LEN", "4096"))
SUMMARY_MAX_TOKENS = int(os.getenv("SUMMARY_MAX_TOKENS", "256"))
SAFETY_MARGIN_TOK = int(os.getenv("SAFETY_MARGIN_TOK", "128"))

# LibreChat common alias pattern
MODEL_ALIASES = {
    "local/main": MAIN_MODEL,
    "main": MAIN_MODEL,
}

# ----------------------------
# Logging (JSON lines)
# ----------------------------

def _ts() -> float:
    return time.time()

def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": _ts(), "level": level.upper(), "msg": msg, **fields}
    print(json.dumps(rec, ensure_ascii=False), flush=True)

# ----------------------------
# Token counting (best-effort)
# ----------------------------

class TokenCounter:
    """Simple token estimator.

    - Uses tiktoken(cl100k_base) if available
    - Fallback: chars/4
    """
    def __init__(self) -> None:
        self._enc = None
        try:
            import tiktoken  # type: ignore
            self._enc = tiktoken.get_encoding("cl100k_base")
            self.mode = "tiktoken:cl100k_base"
        except Exception:
            self.mode = "chars/4"

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            return len(self._enc.encode(text))
        return max(1, int(len(text) / 4))

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        # Simple overhead: ~4 tokens per message + content
        total = 0
        for m in messages:
            total += 4
            content = m.get("content", "")
            if isinstance(content, str):
                total += self.count_text(content)
            elif isinstance(content, list):
                # Multimodal: count only text parts, ignore images
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.count_text(part.get("text", ""))
        return total

TOK = TokenCounter()

# ----------------------------
# Upstream helpers
# ----------------------------

_http_client: Optional[httpx.AsyncClient] = None

async def http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
    return _http_client

_ctx_cache: Dict[str, Tuple[int, float]] = {}
_CTX_TTL_SEC = 60.0

def _extract_ctx_len_from_model_obj(obj: Dict[str, Any]) -> Optional[int]:
    candidates: List[int] = []
    for k in ("loaded_context_length", "context_length", "context_window", "n_ctx", "ctx_len", "max_context_length"):
        v = obj.get(k)
        if isinstance(v, int) and v > 0:
            return v, k


async def get_ctx_len_for_model(upstream_model: str) -> int:
    now = time.time()
    cached = _ctx_cache.get(upstream_model)
    if cached and (now - cached[1]) < _CTX_TTL_SEC:
        return cached[0]

    url = f"{UPSTREAM_BASE_URL}/api/v0/models"
    try:
        client = await http_client()
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
        models = data.get("data") if isinstance(data, dict) else None
        
        if not isinstance(models, list):
            raise ValueError("Unexpected /api/v0/models format")

        chosen = None
        for m in models:
            if isinstance(m, dict) and m.get("id") == upstream_model:
                chosen = m
                break
        print(chosen)
        ctx_len, ctx_len_source = _extract_ctx_len_from_model_obj(chosen)

        if ctx_len is None:
            # best-effort fallback: try first model entry
            for m in models:
                if isinstance(m, dict):
                    ctx_len, ctx_len_source = _extract_ctx_len_from_model_obj(m)
                    if ctx_len:
                        break

        if ctx_len is None:
            ctx_len = DEFAULT_CTX_LEN

        _ctx_cache[upstream_model] = (ctx_len, now)
        log("INFO", "ctx_len", upstream_model=upstream_model, ctx_len=ctx_len, source="/api/v0/models" if not ctx_len_source is None else "default", field=ctx_len_source)
        return ctx_len
    except Exception as e:
        log("WARN", "ctx_len_fallback", upstream_model=upstream_model, ctx_len=DEFAULT_CTX_LEN, err=str(e))
        _ctx_cache[upstream_model] = (DEFAULT_CTX_LEN, now)
        return DEFAULT_CTX_LEN

def map_model(client_model: str) -> str:
    return MODEL_ALIASES.get(client_model, client_model)

# ----------------------------
# Summarization logic
# ----------------------------

def split_messages(messages: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if not system_msgs:
        return None, non_system

    merged = ""
    for sm in system_msgs:
        c = sm.get("content", "")
        if isinstance(c, str) and c.strip():
            merged += c.strip() + "\n\n"
    merged = merged.strip()

    return {"role": "system", "content": merged}, non_system

def render_messages_for_summary(messages: List[Dict[str, Any]], max_chars: int = 12000) -> str:
    lines: List[str] = []
    used = 0
    for m in messages:
        role = (m.get("role") or "unknown").upper()
        content = m.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: List[str] = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
            text = "\n".join(parts)
        else:
            text = ""

        text = text.strip()
        if not text:
            continue

        line = f"{role}: {text}"
        lines.append(line)
        used += len(line)
        if used > max_chars:
            lines.append("... (truncated)")
            break
    return "\n".join(lines)

async def summarize_middle(middle: List[Dict[str, Any]], req_id: str) -> str:
    transcript = render_messages_for_summary(middle)

    sys = (
        "Sei un assistente che produce un RIASSUNTO DI CONTESTO per un altro modello.\n"
        "Obiettivo: comprimere la parte centrale preservando fatti, nomi, decisioni, richieste, vincoli e TODO.\n"
        "Regole: non inventare; mantieni lingua coerente (preferisci italiano).\n"
        "Output: breve e denso; bullet points se utile; includi decisioni e TODO.\n"
    )
    user = (
        "Riassumi la parte centrale della conversazione qui sotto.\n\n"
        "=== TRANSCRIPT START ===\n"
        f"{transcript}\n"
        "=== TRANSCRIPT END ===\n\n"
        "RISPOSTA (solo riassunto):"
    )

    body = {
        "model": SUMMARY_MODEL,
        "messages": [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "stream": False,
    }

    url = f"{UPSTREAM_BASE_URL}/chat/completions"
    t0 = time.time()
    client = await http_client()
    r = await client.post(url, json=body)
    elapsed_ms = (time.time() - t0) * 1000.0
    r.raise_for_status()

    data = r.json()
    try:
        summary = data["choices"][0]["message"]["content"]
    except Exception:
        summary = ""

    summary = (summary or "").strip() or "(Riassunto non disponibile.)"
    log("INFO", "summary_done", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), usage=data.get("usage"))
    return summary

def build_repacked_messages(original: List[Dict[str, Any]], summary_text: str) -> List[Dict[str, Any]]:
    sys_msg, non_system = split_messages(original)

    head = non_system[:2]
    tail = non_system[-2:] if len(non_system) > 2 else non_system[2:]
    middle = non_system[2:-2] if len(non_system) > 4 else []

    if not middle:
        merged: List[Dict[str, Any]] = []
        if sys_msg:
            merged.append(sys_msg)
        merged.extend(non_system)
        return merged

    sys_text = ""
    if sys_msg and isinstance(sys_msg.get("content"), str) and sys_msg["content"].strip():
        sys_text += sys_msg["content"].strip() + "\n\n"

    sys_text += "### CONTEXT SUMMARY (auto)\n" + summary_text.strip()

    repacked: List[Dict[str, Any]] = [{"role": "system", "content": sys_text.strip()}]
    repacked.extend(head)
    repacked.append({"role": "system", "content": "### CONTEXT CONTINUES (latest turns below)"})
    repacked.extend(tail)
    return repacked

# ----------------------------
# FastAPI app
# ----------------------------

app = FastAPI()

@app.on_event("shutdown")
async def _shutdown() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None

@app.post("/v1/chat/completions")
async def chat_completions(req: Request) -> Response:
    req_id = os.urandom(6).hex()
    payload = await req.json()

    client_model = payload.get("model", MAIN_MODEL)
    upstream_model = map_model(client_model)

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": {"message": "Invalid payload: messages must be a list"}}, status_code=400)

    stream = bool(payload.get("stream", False))

    max_tokens_req = payload.get("max_tokens")
    max_out = int(max_tokens_req) if isinstance(max_tokens_req, int) and max_tokens_req > 0 else 900

    ctx_eff = await get_ctx_len_for_model(upstream_model)
    threshold = max(256, ctx_eff - max_out - SAFETY_MARGIN_TOK)

    prompt_tok_est = TOK.count_messages(messages)

    log(
        "INFO",
        "http_in",
        req_id=req_id,
        model=client_model,
        upstream_model=upstream_model,
        stream=stream,
        ctx_eff=ctx_eff,
        threshold=threshold,
        prompt_tok_est=prompt_tok_est,
        msg_count=len(messages),
        token_mode=TOK.mode,
    )

    did_summarize = False
    repacked_messages = messages

    if prompt_tok_est > threshold and len(messages) >= 6:
        sys_msg, non_system = split_messages(messages)
        middle = non_system[2:-2] if len(non_system) > 4 else []
        log(
            "INFO",
            "summary_needed",
            req_id=req_id,
            prompt_tok_est=prompt_tok_est,
            threshold=threshold,
            non_system_count=len(non_system),
            middle_count=len(middle),
        )

        try:
            summary_text = await summarize_middle(middle, req_id=req_id)
            repacked_messages = build_repacked_messages(messages, summary_text=summary_text)
            did_summarize = True
            repacked_tok_est = TOK.count_messages(repacked_messages)
            log(
                "INFO",
                "repacked",
                req_id=req_id,
                did_summarize=True,
                repacked_msg_count=len(repacked_messages),
                repacked_tok_est=repacked_tok_est,
            )
        except Exception as e:
            log("ERROR", "summary_failed_fallback_passthrough", req_id=req_id, err=str(e))
            repacked_messages = messages
            did_summarize = False

    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model
    upstream_payload["messages"] = repacked_messages
    print(upstream_payload)
    url = f"{UPSTREAM_BASE_URL}/chat/completions"
    client = await http_client()

    if stream:
        async def _iter():
            try:
                async with client.stream("POST", url, json=upstream_payload) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes():
                        if chunk:
                            yield chunk
            except httpx.HTTPStatusError as e:
                err_text = e.response.text
                log("ERROR", "upstream_http_error_stream", req_id=req_id, status=e.response.status_code, body=err_text[:500], did_summarize=did_summarize)
                yield (f"data: {json.dumps({'error': {'message': 'Upstream error', 'details': err_text}})}\n\n").encode("utf-8")
                yield b"data: [DONE]\n\n"
            except Exception as e:
                log("ERROR", "upstream_stream_exception", req_id=req_id, err=str(e), did_summarize=did_summarize)
                yield (f"data: {json.dumps({'error': {'message': 'Upstream stream exception', 'details': str(e)}})}\n\n").encode("utf-8")
                yield b"data: [DONE]\n\n"

        log("INFO", "upstream_stream_start", req_id=req_id, did_summarize=did_summarize)
        headers = {
            "content-type": "text/event-stream; charset=utf-8",
            "cache-control": "no-cache",
            "connection": "keep-alive",
        }
        return StreamingResponse(_iter(), headers=headers)

    t0 = time.time()
    try:
        r = await client.post(url, json=upstream_payload)
        elapsed_ms = (time.time() - t0) * 1000.0
        if r.status_code >= 400:
            log("ERROR", "upstream_http_error", req_id=req_id, status=r.status_code, body=r.text[:800], did_summarize=did_summarize)
            return JSONResponse({"error": {"message": "Upstream error", "details": r.text}}, status_code=r.status_code)

        data = r.json()
        log("INFO", "http_out", req_id=req_id, status=200, elapsed_ms=round(elapsed_ms, 2), did_summarize=did_summarize, usage=data.get("usage"))
        return JSONResponse(data, status_code=200)
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        log("ERROR", "proxy_exception", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), err=str(e), did_summarize=did_summarize)
        return JSONResponse({"error": {"message": "Proxy exception", "details": str(e)}}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    log("INFO", "startup", upstream=UPSTREAM_BASE_URL, main_model=MAIN_MODEL, summary_model=SUMMARY_MODEL, default_ctx=DEFAULT_CTX_LEN)
    uvicorn.run("keeprollming_orchestrator:app", host=host, port=port, reload=False)
