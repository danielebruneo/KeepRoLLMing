"""KeepRoLLMing - minimal OpenAI-compatible chat completions orchestrator

Goals (v0 - strip the superfluo):
- Only exposes: POST /v1/chat/completions
- Proxy to an upstream OpenAI-compatible backend (e.g. LM Studio)
- Retrieves (best-effort) effective context length from upstream model metadata (/api/v0/models)
- If the incoming conversation is too long for the model context:
    Keep: system prompt (if present) + first 2 msgs (head) + last 2 msgs (tail)
    Summarize: everything in the middle via a smaller model (non-streaming)
    Repack and send to upstream main model
- Prioritize clarity + simple logging (JSON lines)

New (profiles + passthrough):
- Profiles (client model aliases):
    * local/quick -> QUICK_MAIN_MODEL + QUICK_SUMMARY_MODEL
    * local/main  -> BASE_MAIN_MODEL  + BASE_SUMMARY_MODEL
    * local/deep  -> DEEP_MAIN_MODEL  + DEEP_SUMMARY_MODEL
  (Also supports: quick, main, deep as short aliases.)
- Passthrough:
    * If client model is "pass/MODELNAME" => passthrough to upstream MODELNAME (no summarization).
      Example: "pass/qwen2.5-14b-instruct" or "pass/openai/gpt-4.1" (if your upstream supports it)

Run:
  python keeprollming_orchestrator_profiles.py

Env:
  UPSTREAM_BASE_URL   default: http://127.0.0.1:1234/v1

  # Backwards compatible defaults (used when client sends an explicit model name, not an alias)
  MAIN_MODEL          default: qwen2.5-3b-instruct
  SUMMARY_MODEL       default: qwen2.5-1.5b-instruct

  # Profiles
  QUICK_MAIN_MODEL    default: qwen2.5-3b-instruct
  QUICK_SUMMARY_MODEL default: qwen2.5-1.5b-instruct

  BASE_MAIN_MODEL     default: qwen2.5-7b-instruct
  BASE_SUMMARY_MODEL  default: qwen2.5-3b-instruct

  DEEP_MAIN_MODEL     default: qwen2.5-27b-instruct
  DEEP_SUMMARY_MODEL  default: qwen2.5-7b-instruct

  DEFAULT_CTX_LEN     default: 4096
  SUMMARY_MAX_TOKENS  default: 256
  SAFETY_MARGIN_TOK   default: 128
"""

from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from rich import print_json

# ----------------------------
# Configuration
# ----------------------------

UPSTREAM_BASE_URL = os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")

# Backwards compatible defaults (when client passes an explicit model name, not an alias)
MAIN_MODEL = os.getenv("MAIN_MODEL", "qwen2.5-3b-instruct")
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "qwen2.5-1.5b-instruct")

# Profile defaults (requested)
QUICK_MAIN_MODEL = os.getenv("QUICK_MAIN_MODEL", MAIN_MODEL)
QUICK_SUMMARY_MODEL = os.getenv("QUICK_SUMMARY_MODEL", SUMMARY_MODEL)

BASE_MAIN_MODEL = os.getenv("BASE_MAIN_MODEL", "qwen2.5-v1-7b-instruct")
BASE_SUMMARY_MODEL = os.getenv("BASE_SUMMARY_MODEL", "qwen2.5-3b-instruct")

DEEP_MAIN_MODEL = os.getenv("DEEP_MAIN_MODEL", "qwen2.5-27b-instruct")
DEEP_SUMMARY_MODEL = os.getenv("DEEP_SUMMARY_MODEL", "qwen2.5-7b-instruct")

DEFAULT_CTX_LEN = int(os.getenv("DEFAULT_CTX_LEN", "4096"))
SUMMARY_MAX_TOKENS = int(os.getenv("SUMMARY_MAX_TOKENS", "256"))
SAFETY_MARGIN_TOK = int(os.getenv("SAFETY_MARGIN_TOK", "128"))

# Max chars for logging large payloads (input conversation, summary requests, etc.)
LOG_PAYLOAD_MAX_CHARS = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", "20000000"))

PASSTHROUGH_PREFIX = "pass/"

@dataclass(frozen=True)
class Profile:
    name: str
    main_model: str
    summary_model: str

PROFILES: Dict[str, Profile] = {
    "quick": Profile("quick", QUICK_MAIN_MODEL, QUICK_SUMMARY_MODEL),
    "main":  Profile("main",  BASE_MAIN_MODEL,  BASE_SUMMARY_MODEL),
    "deep":  Profile("deep",  DEEP_MAIN_MODEL,  DEEP_SUMMARY_MODEL),
}

# Client-facing model aliases (LibreChat or your own)
MODEL_ALIASES: Dict[str, str] = {
    "local/quick": "quick",
    "quick": "quick",

    "local/main": "main",
    "main": "main",

    "local/deep": "deep",
    "deep": "deep",
}

# ----------------------------
# Logging (JSON lines)
# ----------------------------

def _ts() -> float:
    return time.time()

def log(level: str, msg: str, **fields: Any) -> None:
    rec = {"ts": _ts(), "level": level.upper(), "msg": msg, **fields}
    #print(json.dumps(rec, ensure_ascii=False, indent=4), flush=True)
    print_json(data=rec)


MAX_BODY_CHARS = 4000  # tweak

def _snip(s: str, limit: int = MAX_BODY_CHARS) -> str:
    return s
    if s is None:
        return ""
    return s if len(s) <= limit else (s[:limit] + f"... <snip {len(s)-limit} chars>")

def _is_json_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    ct = content_type.split(";", 1)[0].strip().lower()
    return ct == "application/json" or ct.endswith("+json")

def _decode_body(content: bytes) -> str:
    # try utf-8; fall back with replacement to avoid exceptions
    return content.decode("utf-8", errors="replace")

async def log_request(req: httpx.Request) -> None:
    content_type = req.headers.get("content-type")
    body_repr: object | str = ""

    # NOTE: req.content is bytes. For huge uploads you may want to skip logging.
    content = req.content or b""

    if content:
        if _is_json_content_type(content_type):
            try:
                decoded = _decode_body(content)
                body_repr = json.loads(decoded)  # object (dict/list/etc)
            except Exception:
                # Not valid JSON; log decoded text instead
                body_repr = _snip(_decode_body(content))
        else:
            # Non-JSON: log text-ish content, snipped
            body_repr = _snip(_decode_body(content))

    log(
        "INFO",
        "request_sent",
        url=str(req.url),
        method=req.method,
        headers=dict(req.headers),
        body=snip_json(body_repr) if isinstance(body_repr, (dict, list)) else body_repr,
    )

def snip_json(obj: Any, max_chars: int = LOG_PAYLOAD_MAX_CHARS) -> str:
    return obj
    """Best-effort JSON rendering for logs (never raises)."""
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        try:
            s = str(obj)
        except Exception:
            s = "<unserializable>"
    if max_chars and len(s) > max_chars:
        return s[:max_chars] + "…"
    return s

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
_http_client: httpx.AsyncClient | None = None

MAX_SSE_BYTES = 8000  # capture only the first N bytes of SSE bodies for logging


async def http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:

        async def _on_request(request: httpx.Request) -> None:
            # mettiamo start time per calcolare elapsed nel response hook
            request.extensions["ts_start"] = time.perf_counter()
            await log_request(request)

        async def _on_response(response: httpx.Response) -> None:
            ts_start = response.request.extensions.get("ts_start")
            elapsed_ms = (time.perf_counter() - ts_start) * 1000.0 if ts_start else None

            ct = (response.headers.get("content-type") or "").lower()

            # For SSE / streaming responses, the body is not read here. We only log metadata.
            if ct.startswith("text/event-stream"):
                log(
                    "INFO",
                    "response_received",
                    url=str(response.request.url),
                    method=response.request.method,
                    status=response.status_code,
                    elapsed_ms=elapsed_ms,
                    headers=dict(response.headers),
                    body="",
                    note="SSE response headers received (body logged by stream tee when consumed)",
                )
                return

            # Non-streaming: body is generally available (client.post/get reads it).
            await log_response(response, elapsed_ms=elapsed_ms)

        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )

    return _http_client


async def log_response(r: httpx.Response, elapsed_ms: float | None = None) -> None:
    content_type = r.headers.get("content-type")
    body_repr: object | str = ""

    try:
        content = r.content or b""
    except httpx.ResponseNotRead:
        # Response was streamed and not read yet
        content = b""

    if content:
        if _is_json_content_type(content_type):
            try:
                body_repr = r.json()
            except Exception:
                body_repr = _snip(content.decode("utf-8", errors="replace"))
        else:
            body_repr = _snip(content.decode("utf-8", errors="replace"))

    log(
        "INFO",
        "response_received_fully",
        url=str(r.request.url),
        method=r.request.method,
        status=r.status_code,
        elapsed_ms=elapsed_ms,
        headers=dict(r.headers),
        body=body_repr,
        #body=snip_json(body_repr) if isinstance(body_repr, (dict, list)) else body_repr,
    )

async def log_streaming_response(
    r: httpx.Response,
    captured_bytes: bytes,
    *,
    elapsed_ms: float | None = None,
) -> None:
    text = captured_bytes.decode("utf-8", errors="replace")
    try:
        log(
            "INFO",
            "response_received",
            url=str(r.request.url),
            method=r.request.method,
            status=r.status_code,
            elapsed_ms=elapsed_ms,
            headers=dict(r.headers),
            body=json.loads(text),  # volendo _snip(text)
            note=f"streamed (captured {len(captured_bytes)} bytes)",
        )
    except:
        log(
            "INFO",
            "response_received",
            url=str(r.request.url),
            method=r.request.method,
            status=r.status_code,
            elapsed_ms=elapsed_ms,
            headers=dict(r.headers),
            body=_snip(text),  # volendo _snip(text)
            note=f"streamed (captured {len(captured_bytes)} bytes)",
        )


_ctx_cache: Dict[str, Tuple[int, float]] = {}
_CTX_TTL_SEC = 60.0

def _extract_ctx_len_from_model_obj(obj: Optional[Dict[str, Any]]) -> Optional[Tuple[int, str]]:
    if not isinstance(obj, dict):
        return None
    for k in ("loaded_context_length", "context_length", "context_window", "n_ctx", "ctx_len", "max_context_length"):
        v = obj.get(k)
        if isinstance(v, int) and v > 0:
            return (v, k)
    return None

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
        ctx_tuple = _extract_ctx_len_from_model_obj(chosen)
        if ctx_tuple is None:
            # best-effort fallback: try first model entry with a ctx field
            for m in models:
                ctx_tuple = _extract_ctx_len_from_model_obj(m if isinstance(m, dict) else None)
                if ctx_tuple:
                    break

        if ctx_tuple is None:
            ctx_len = DEFAULT_CTX_LEN
            ctx_src = "default"
        else:
            ctx_len, ctx_src = ctx_tuple

        _ctx_cache[upstream_model] = (ctx_len, now)
        log(
            "INFO",
            "ctx_len",
            upstream_model=upstream_model,
            ctx_len=ctx_len,
            source=f"/api/v0/models:{ctx_src}" if ctx_len != DEFAULT_CTX_LEN else "default",
        )
        return ctx_len
    except Exception as e:
        log("WARN", "ctx_len_fallback", upstream_model=upstream_model, ctx_len=DEFAULT_CTX_LEN, err=str(e))
        _ctx_cache[upstream_model] = (DEFAULT_CTX_LEN, now)
        return DEFAULT_CTX_LEN

def _resolve_profile_and_models(client_model: str) -> Tuple[Optional[Profile], str, str, bool]:
    """Return (profile_or_none, upstream_main_model, summary_model, passthrough_enabled)."""
    if isinstance(client_model, str) and client_model.startswith(PASSTHROUGH_PREFIX):
        backend = client_model[len(PASSTHROUGH_PREFIX):].strip()
        # If empty, fallback to MAIN_MODEL but keep passthrough disabled to avoid surprises
        if not backend:
            return (None, MAIN_MODEL, SUMMARY_MODEL, False)
        return (None, backend, SUMMARY_MODEL, True)

    key = MODEL_ALIASES.get(client_model)
    if key and key in PROFILES:
        p = PROFILES[key]
        return (p, p.main_model, p.summary_model, False)

    # No alias: treat it as an explicit upstream model name (backwards-compatible)
    return (None, client_model or MAIN_MODEL, SUMMARY_MODEL, False)

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

async def summarize_middle(middle: List[Dict[str, Any]], req_id: str, summary_model: str) -> str:
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
        "model": summary_model,
        "messages": [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        "temperature": 0.4,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "stream": False,
    }

    log(
        "INFO",
        "summary_req",
        req_id=req_id,
        summary_model=summary_model,
        middle_count=len(middle),
        transcript_chars=len(transcript),
        body_json=snip_json(body),
    )

    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
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
    log(
        "INFO",
        "summary_reply",
        req_id=req_id,
        elapsed_ms=round(elapsed_ms, 2),
        usage=data.get("usage"),
        summary_chars=len(summary),
        summary_snip=snip_json(summary, max_chars=min(LOG_PAYLOAD_MAX_CHARS, 4000)),
        raw_json=snip_json(data),
    )
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

    sum_text = summary_text.strip()

    repacked: List[Dict[str, Any]] = [{"role": "system", "content": sys_text.strip()}]
    repacked.extend(head)
    repacked.append({"role": "system", "content": "riassunto messaggi intermedi:\n{}".format(sum_text)})
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

    log("INFO", "payload_in_full", req_id=req_id, body_json=snip_json(payload))

    client_model = payload.get("model", MAIN_MODEL)
    profile, upstream_model, summary_model, is_passthrough = _resolve_profile_and_models(client_model)

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
        client_model=client_model,
        profile=(profile.name if profile else None),
        passthrough=is_passthrough,
        upstream_model=upstream_model,
        summary_model=summary_model,
        stream=stream,
        ctx_eff=ctx_eff,
        threshold=threshold,
        prompt_tok_est=prompt_tok_est,
        msg_count=len(messages),
        token_mode=TOK.mode,
    )

    did_summarize = False
    repacked_messages = messages

    if (not is_passthrough) and prompt_tok_est > threshold and len(messages) >= 6:
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
            summary_model=summary_model,
        )

        try:
            summary_text = await summarize_middle(middle, req_id=req_id, summary_model=summary_model)
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
            # Fail-open: passthrough original messages (no summary)
            log("ERROR", "summary_failed_fallback_passthrough", req_id=req_id, err=str(e))
            repacked_messages = messages
            did_summarize = False

    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model
    upstream_payload["messages"] = repacked_messages

    log(
        "INFO",
        "upstream_req_repacked",
        req_id=req_id,
        did_summarize=did_summarize,
        passthrough=is_passthrough,
        upstream_url=f"{UPSTREAM_BASE_URL}/v1/chat/completions",
        body_json=snip_json(upstream_payload),
    )

    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    client = await http_client()

    if stream:
        async def _iter():
            t0 = time.perf_counter()
            captured = bytearray()
            r: httpx.Response | None = None
            try:
                async with client.stream("POST", url, json=upstream_payload) as resp:
                    r = resp
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            # capture a snippet for logging
                            if len(captured) < MAX_SSE_BYTES:
                                take = min(len(chunk), MAX_SSE_BYTES - len(captured))
                                captured.extend(chunk[:take])
                            yield chunk
            except httpx.HTTPStatusError as e:
                err_text = e.response.text
                log(
                    "ERROR",
                    "upstream_http_error_stream",
                    req_id=req_id,
                    status=e.response.status_code,
                    body=err_text[:500],
                    did_summarize=did_summarize,
                    passthrough=is_passthrough,
                )
                yield (f"data: {json.dumps({'error': {'message': 'Upstream error', 'details': err_text}})}\n\n").encode("utf-8")
                yield b"data: [DONE]\n\n"
            except Exception as e:
                log(
                    "ERROR",
                    "upstream_stream_exception",
                    req_id=req_id,
                    err=str(e),
                    did_summarize=did_summarize,
                    passthrough=is_passthrough,
                )
                yield (f"data: {json.dumps({'error': {'message': 'Upstream stream exception', 'details': str(e)}})}\n\n").encode("utf-8")
                yield b"data: [DONE]\n\n"
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                # If we did receive a response object, log the captured SSE snippet.
                if r is not None:
                    try:
                        await log_streaming_response(r, bytes(captured), elapsed_ms=elapsed_ms)
                    except Exception as _e:
                        # never let logging break streaming
                        log("WARN", "stream_log_failed", req_id=req_id, err=str(_e))

        log("INFO", "upstream_stream_start", req_id=req_id, did_summarize=did_summarize, passthrough=is_passthrough)
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
            log(
                "ERROR",
                "upstream_http_error",
                req_id=req_id,
                status=r.status_code,
                body=r.text[:800],
                did_summarize=did_summarize,
                passthrough=is_passthrough,
            )
            return JSONResponse({"error": {"message": "Upstream error", "details": r.text}}, status_code=r.status_code)

        data = r.json()
        log(
            "INFO",
            "http_out",
            req_id=req_id,
            status=200,
            elapsed_ms=round(elapsed_ms, 2),
            did_summarize=did_summarize,
            passthrough=is_passthrough,
            usage=data.get("usage"),
            data=data
        )
        return JSONResponse(data, status_code=200)
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        log(
            "ERROR",
            "proxy_exception",
            req_id=req_id,
            elapsed_ms=round(elapsed_ms, 2),
            err=str(e),
            did_summarize=did_summarize,
            passthrough=is_passthrough,
        )
        raise e
        return JSONResponse({"error": {"message": "Proxy exception", "details": str(e)}}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    log(
        "INFO",
        "startup",
        upstream=UPSTREAM_BASE_URL,
        main_model=MAIN_MODEL,
        summary_model=SUMMARY_MODEL,
        profiles={
            "quick": {"main": QUICK_MAIN_MODEL, "summary": QUICK_SUMMARY_MODEL},
            "main": {"main": BASE_MAIN_MODEL, "summary": BASE_SUMMARY_MODEL},
            "deep": {"main": DEEP_MAIN_MODEL, "summary": DEEP_SUMMARY_MODEL},
        },
        default_ctx=DEFAULT_CTX_LEN,
    )
    uvicorn.run("keeprollming_orchestrator_profiles:app", host=host, port=port, reload=False)