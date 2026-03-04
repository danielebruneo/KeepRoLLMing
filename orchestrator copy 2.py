#!/usr/bin/env python3
"""
LibreChat Rolling-Summary Orchestrator — v3.3 (requests-only) — LibreChat payload compatibility

Fixes / additions:
- Logs detailed validation errors (invalid_payload) with payload snippet.
- Normalizes message content to plain string:
    - content as list (OpenAI "content parts") -> join text parts
    - content as dict -> pull text/content/value
    - None -> ""
- Accepts extra keys in payload/messages (Pydantic Config: extra="allow")
- Keeps CLEAN HEAD behavior: no internal MAIN_SYSTEM; head = only incoming system messages (trimmed).
- Keeps debugging: middleware, upstream logging, /debug/last_upstream.

Env reminders:
- LITELLM_BASE MUST include /v1
- If bypassing LiteLLM, set MAIN_MODEL / SUMMARY_MODEL to real LM Studio model ids.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional, Tuple
import time
import hashlib
import os
import requests
import logging
import json
import uuid

# -----------------------
# Endpoints / Models
# -----------------------
LMSTUDIO_BASE = os.getenv("LMSTUDIO_BASE", "http://127.0.0.1:1234")
LITELLM_BASE = os.getenv("LITELLM_BASE", "http://127.0.0.1:4000/v1")  # MUST include /v1

SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "local/summary")
MAIN_MODEL = os.getenv("MAIN_MODEL", "local/main")

LM_MAIN_ID = os.getenv("LM_MAIN_ID", "qwen2.5-3b-instruct")
LM_SUMMARY_ID = os.getenv("LM_SUMMARY_ID", "qwen2.5-1.5b-instruct")

LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "x")

# -----------------------
# Budgeting
# -----------------------
CTX_TTL_SEC = int(os.getenv("CTX_TTL_SEC", "20"))

MIN_OUT = int(os.getenv("MIN_OUT", "64"))
MIN_INPUT = int(os.getenv("MIN_INPUT", "512"))

DEFAULT_OUT_CAP = int(os.getenv("DEFAULT_OUT_CAP", "900"))
DEFAULT_OUT_FRAC = float(os.getenv("DEFAULT_OUT_FRAC", "0.22"))

MARGIN_FRAC = float(os.getenv("MARGIN_FRAC", "0.05"))
MARGIN_MIN = int(os.getenv("MARGIN_MIN", "96"))

HEAD_FRAC = float(os.getenv("HEAD_FRAC", "0.07"))
SUMMARY_FRAC = float(os.getenv("SUMMARY_FRAC", "0.27"))
TAIL_FRAC = float(os.getenv("TAIL_FRAC", "0.27"))

TAIL_KEEP = int(os.getenv("TAIL_KEEP", "6"))
TAIL_MAX_CANDIDATE = int(os.getenv("TAIL_MAX_CANDIDATE", "14"))
HARD_HEAD_CHAR_CAP = int(os.getenv("HARD_HEAD_CHAR_CAP", "6000"))

# -----------------------
# Debug / Metrics
# -----------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_JSON = os.getenv("LOG_JSON", "0") == "1"

DEBUG_ENABLE_ENDPOINTS = os.getenv("DEBUG_ENABLE_ENDPOINTS", "0") == "1"
DEBUG_ADD_HEADERS = os.getenv("DEBUG_ADD_HEADERS", "1") == "1"

DEBUG_UPSTREAM = os.getenv("DEBUG_UPSTREAM", "0") == "1"
DEBUG_UPSTREAM_BODY_CHARS = int(os.getenv("DEBUG_UPSTREAM_BODY_CHARS", "1200"))

EMA_ALPHA = float(os.getenv("EMA_ALPHA", "0.2"))

HEAD_ANCHORS = os.getenv("HEAD_ANCHORS", "").strip()

SUMMARY_SYSTEM = os.getenv("SUMMARY_SYSTEM", "").strip() or (
    "Sei un modulo di memoria. Aggiorna una memoria compatta e fedele della conversazione.\n"
    "Regole:\n"
    "- Non inventare dettagli.\n"
    "- Se un punto è incerto, scrivi 'INCERTO'.\n"
    "- Mantieni numeri, vincoli tecnici, decisioni, TODO e nomi.\n"
    "Output in italiano, conciso, massimo ~900 token, in 5 sezioni:\n"
    "1) OBIETTIVO ATTUALE\n"
    "2) FATTI STABILI / VINCOLI\n"
    "3) DECISIONI PRESE\n"
    "4) TODO / PROSSIMI PASSI\n"
    "5) TERMINI / NOMI IMPORTANTI\n"
)

# -----------------------
# Logging
# -----------------------
logger = logging.getLogger("orchestrator")
logger.propagate = False
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
handler = logging.StreamHandler()

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"ts": time.time(), "level": record.levelname, "msg": record.getMessage()}
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            base.update(extra)
        return json.dumps(base, ensure_ascii=False)

handler.setFormatter(JsonFormatter() if LOG_JSON else logging.Formatter("%(asctime)s %(levelname)s - %(message)s"))
logger.handlers = [handler]

def _t(text: str, n: int) -> str:
    if text is None:
        return ""
    return text if len(text) <= n else text[:n] + "…"

# -----------------------
# App + State
# -----------------------
app = FastAPI(title="LibreChat Orchestrator (requests-only)", version="3.3")

STATE: Dict[str, Dict[str, Any]] = {}
CTX_CACHE: Dict[str, Tuple[int, float]] = {}
LAST_UPSTREAM: Dict[str, Dict[str, Any]] = {"main": {}, "summary": {}}

METRICS: Dict[str, Dict[str, Any]] = {
    "summary": {"calls": 0, "avg_ms": 0.0, "ema_tok_s": 0.0, "avg_tok_s": 0.0, "tok_sum": 0, "sec_sum": 0.0},
    "main":    {"calls": 0, "avg_ms": 0.0, "ema_tok_s": 0.0, "avg_tok_s": 0.0, "tok_sum": 0, "sec_sum": 0.0},
    "ctx":     {"reads": 0, "avg_ms": 0.0, "last_ctx_main": None, "last_ctx_summary": None, "last_ctx_eff": None},
}

# Middleware: request start/end logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.time()
    logger.info("http_in", extra={"extra": {"req_id": req_id, "method": request.method, "path": request.url.path}})
    try:
        response = await call_next(request)
    except Exception as e:
        logger.exception("http_err", extra={"extra": {"req_id": req_id, "err": str(e)}})
        raise
    dt = (time.time() - t0) * 1000.0
    response.headers["X-Req-Id"] = response.headers.get("X-Req-Id") or req_id
    logger.info("http_out", extra={"extra": {"req_id": req_id, "status": response.status_code, "ms": round(dt, 2)}})
    return response

# -----------------------
# Normalization helpers (LibreChat compatibility)
# -----------------------
def normalize_content(c: Any) -> str:
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts: List[str] = []
        for item in c:
            if item is None:
                continue
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                    continue
                for k in ("text", "content", "value"):
                    if k in item and item[k] is not None:
                        parts.append(str(item[k]))
                        break
        return "\n".join([p for p in parts if p]).strip()
    if isinstance(c, dict):
        for k in ("text", "content", "value"):
            if k in c and c[k] is not None:
                return str(c[k])
        return json.dumps(c, ensure_ascii=False)
    return str(c)

def normalize_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    msgs = payload.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict):
                m["content"] = normalize_content(m.get("content"))
    return payload

# -----------------------
# Utilities
# -----------------------
def rough_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def estimate_messages_tokens(messages: List[Dict[str, str]]) -> int:
    return sum(rough_tokens(f"{m.get('role','')}:{m.get('content','')}") for m in messages)

def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(n, hi))

def safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def hash_key(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:16]

def get_conv_key(payload: dict, request: Request) -> str:
    for k in ("conversation_id", "conversationId", "thread_id", "threadId", "chat_id", "chatId"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    meta = payload.get("metadata") or payload.get("user") or {}
    if isinstance(meta, dict):
        for k in ("conversation_id", "conversationId", "thread_id", "threadId"):
            v = meta.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    msgs = payload.get("messages") or []
    first_user = ""
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "user":
            first_user = safe_str(m.get("content"))[:200]
            break
    ua = request.headers.get("user-agent", "")[:80]
    base = f"{payload.get('model','')}|{ua}|{first_user}"
    return hash_key(base)

def _metrics_update(kind: str, elapsed_s: float, out_tokens_est: int) -> float:
    m = METRICS[kind]
    m["calls"] += 1
    ms = elapsed_s * 1000.0
    m["avg_ms"] = (m["avg_ms"] * (m["calls"] - 1) + ms) / m["calls"]
    tok_s = (out_tokens_est / elapsed_s) if elapsed_s > 0 else 0.0
    m["tok_sum"] += int(out_tokens_est)
    m["sec_sum"] += float(elapsed_s)
    m["avg_tok_s"] = (m["tok_sum"] / m["sec_sum"]) if m["sec_sum"] > 0 else 0.0
    m["ema_tok_s"] = (tok_s if m["calls"] == 1 else (EMA_ALPHA * tok_s + (1 - EMA_ALPHA) * m["ema_tok_s"]))
    return tok_s

def get_ctx_from_lmstudio(model_id: str) -> int:
    now = time.time()
    cached = CTX_CACHE.get(model_id)
    if cached and (now - cached[1] < CTX_TTL_SEC):
        return cached[0]
    t0 = time.time()
    ctx_val = None
    try:
        r = requests.get(f"{LMSTUDIO_BASE}/api/v0/models", timeout=2.0)
        r.raise_for_status()
        j = r.json()
        for m in j.get("data", []):
            if m.get("id") == model_id:
                ctx_val = m.get("loaded_context_length") or m.get("max_context_length")
                break
    except Exception as e:
        logger.warning("ctx_read_failed", extra={"extra": {"err": str(e), "model_id": model_id}})
    elapsed = time.time() - t0
    METRICS["ctx"]["reads"] += 1
    METRICS["ctx"]["avg_ms"] = (METRICS["ctx"]["avg_ms"] * (METRICS["ctx"]["reads"] - 1) + elapsed * 1000.0) / METRICS["ctx"]["reads"]
    if isinstance(ctx_val, int) and ctx_val > 0:
        CTX_CACHE[model_id] = (ctx_val, now)
        return ctx_val
    fallback_ctx = 4096
    CTX_CACHE[model_id] = (fallback_ctx, now)
    return fallback_ctx

def compute_budgets(ctx_eff: int, requested_max_tokens: Optional[int]) -> Dict[str, int]:
    margin = max(MARGIN_MIN, int(ctx_eff * MARGIN_FRAC))
    out = min(DEFAULT_OUT_CAP, int(ctx_eff * DEFAULT_OUT_FRAC)) if requested_max_tokens is None else int(requested_max_tokens)
    max_out_allowed = max(MIN_OUT, ctx_eff - margin - MIN_INPUT)
    out = clamp(out, MIN_OUT, max_out_allowed)
    input_budget = max(MIN_INPUT, ctx_eff - margin - out)
    head = int(input_budget * HEAD_FRAC)
    summary = int(input_budget * SUMMARY_FRAC)
    tail = int(input_budget * TAIL_FRAC)
    rag = max(0, input_budget - head - summary - tail)
    return {"ctx": ctx_eff, "margin": margin, "out": out, "input": input_budget, "head": head, "summary": summary, "tail": tail, "rag": rag}

def trim_text_to_tokens(text: str, token_budget: int) -> str:
    if rough_tokens(text) <= token_budget:
        return text
    return text[: max(0, token_budget * 4) ]

def pack_head(system_messages: List[Dict[str, str]], head_budget_tokens: int) -> List[Dict[str, str]]:
    sys_texts: List[str] = []
    for m in system_messages:
        if isinstance(m, dict):
            c = safe_str(m.get("content"))
            if c:
                sys_texts.append(c)

    combined = ("\n\n".join(sys_texts))[:HARD_HEAD_CHAR_CAP]
    combined = trim_text_to_tokens(combined, max(0, head_budget_tokens - (rough_tokens(HEAD_ANCHORS) if HEAD_ANCHORS else 0) - 32))

    head_msgs: List[Dict[str, str]] = []
    if combined.strip():
        head_msgs.append({"role": "system", "content": combined})

    if HEAD_ANCHORS:
        head_msgs.append({"role": "system", "content": trim_text_to_tokens(HEAD_ANCHORS, max(16, head_budget_tokens // 3))})

    while estimate_messages_tokens(head_msgs) > head_budget_tokens and head_msgs:
        last = head_msgs[-1]["content"]
        head_msgs[-1]["content"] = trim_text_to_tokens(last, max(16, rough_tokens(last) - 64))
        if rough_tokens(head_msgs[-1]["content"]) <= 16 and len(head_msgs) > 1:
            head_msgs.pop()
        else:
            break
    return head_msgs

def keep_tail_within_budget(chat_messages: List[Dict[str, str]], tail_budget_tokens: int) -> List[Dict[str, str]]:
    msgs = list(chat_messages)
    if not msgs:
        return msgs
    while estimate_messages_tokens(msgs) > tail_budget_tokens and len(msgs) > 2:
        msgs.pop(0)
    if estimate_messages_tokens(msgs) > tail_budget_tokens and msgs:
        msgs[0]["content"] = trim_text_to_tokens(safe_str(msgs[0].get("content")), max(16, tail_budget_tokens // 2))
    return msgs

def litellm_chat(kind: str, model: str, messages: List[Dict[str, str]], temperature: float, max_tokens: int, req_id: str) -> Dict[str, Any]:
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
        "X-Request-Id": req_id,
    }
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}

    if DEBUG_UPSTREAM:
        logger.info("up_req", extra={"extra": {"req_id": req_id, "kind": kind, "url": url, "body": _t(json.dumps(body, ensure_ascii=False), DEBUG_UPSTREAM_BODY_CHARS)}})

    t0 = time.time()
    r = requests.post(url, headers=headers, json=body, timeout=600)
    dt = (time.time() - t0) * 1000.0

    txt = r.text or ""
    if DEBUG_UPSTREAM:
        logger.info("up_res", extra={"extra": {"req_id": req_id, "kind": kind, "status": r.status_code, "ms": round(dt, 2), "text": _t(txt, DEBUG_UPSTREAM_BODY_CHARS)}})

    LAST_UPSTREAM[kind] = {
        "ts": time.time(),
        "req_id": req_id,
        "url": url,
        "status": r.status_code,
        "ms": round(dt, 2),
        "req_body": body if DEBUG_ENABLE_ENDPOINTS else None,
        "res_text": _t(txt, 8000),
    }

    r.raise_for_status()
    return r.json()

def extract_content(j: Dict[str, Any]) -> str:
    try:
        choices = j.get("choices") or []
        if not choices:
            return ""
        c0 = choices[0] or {}
        msg = c0.get("message")
        if isinstance(msg, dict):
            return msg.get("content") or ""
        if "text" in c0:
            return c0.get("text") or ""
        return ""
    except Exception:
        return ""

def update_summary(prev_summary: str, chunk: List[Dict[str, str]], summary_budget_tokens: int, req_id: str) -> Tuple[str, Dict[str, Any]]:
    chunk_text = "\n".join(f"{m.get('role','').upper()}: {safe_str(m.get('content'))}" for m in chunk)
    user_payload = (
        "MEMORIA PRECEDENTE:\n"
        f"{prev_summary or '(vuota)'}\n\n"
        "NUOVI MESSAGGI DA INTEGRARE:\n"
        f"{chunk_text}"
    )
    messages = [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": user_payload}]
    t0 = time.time()
    j = litellm_chat("summary", SUMMARY_MODEL, messages, temperature=0.2, max_tokens=min(950, max(128, summary_budget_tokens + 64)), req_id=req_id)
    elapsed = time.time() - t0
    out_text = extract_content(j)
    if not out_text:
        logger.warning("empty_summary_content", extra={"extra": {"req_id": req_id, "keys": list(j.keys())}})
    out_text = trim_text_to_tokens(out_text, summary_budget_tokens)
    out_tok = rough_tokens(out_text)
    tok_s = _metrics_update("summary", elapsed, out_tok)
    info = {"elapsed_ms": round(elapsed * 1000, 2), "out_tok_est": out_tok, "tok_s_est": round(tok_s, 2)}
    logger.info("summary_call", extra={"extra": {"req_id": req_id, **info}})
    return out_text, info

# -----------------------
# API schemas (allow extra)
# -----------------------
class MessageIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = ""

class ChatCompletionsIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: List[MessageIn]
    temperature: Optional[float] = 0.6
    max_tokens: Optional[int] = Field(default=None)
    stream: Optional[bool] = False

@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": [{"id": MAIN_MODEL, "object": "model"}, {"id": SUMMARY_MODEL, "object": "model"}]}

@app.get("/debug/metrics")
def debug_metrics():
    if not DEBUG_ENABLE_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not found")
    return METRICS

@app.get("/debug/state")
def debug_state():
    if not DEBUG_ENABLE_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not found")
    return {"conversations": list(STATE.keys()), "state": STATE}

@app.get("/debug/last_upstream")
def debug_last_upstream():
    if not DEBUG_ENABLE_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not found")
    return LAST_UPSTREAM

@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    payload = await req.json()
    payload = normalize_messages(payload)
    logger.info("payload_in", extra={"extra": {
        "payload_snip": _t(json.dumps(payload, ensure_ascii=False), 2500),
        "top_keys": list(payload.keys()),
    }})
    try:
        inp = ChatCompletionsIn(**payload)
    except Exception as e:
        logger.error("invalid_payload", extra={"extra": {
            "err": str(e),
            "top_keys": list(payload.keys()),
            "model": payload.get("model"),
            "has_messages": isinstance(payload.get("messages"), list),
            "messages_len": len(payload.get("messages") or []) if isinstance(payload.get("messages"), list) else None,
            "payload_snip": _t(json.dumps(payload, ensure_ascii=False), 2500),
        }})
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    if inp.stream:
        raise HTTPException(status_code=400, detail="stream=true not supported (yet).")

    req_id = req.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    conv_key = get_conv_key(payload, req)
    st = STATE.setdefault(conv_key, {"summary": "", "tail": []})

    t_req0 = time.time()

    ctx_main = get_ctx_from_lmstudio(LM_MAIN_ID)
    ctx_sum = get_ctx_from_lmstudio(LM_SUMMARY_ID)
    ctx_eff = min(ctx_main, ctx_sum)

    METRICS["ctx"]["last_ctx_main"] = ctx_main
    METRICS["ctx"]["last_ctx_summary"] = ctx_sum
    METRICS["ctx"]["last_ctx_eff"] = ctx_eff

    budgets = compute_budgets(ctx_eff, inp.max_tokens)

    # Convert messages to plain dicts with normalized content
    messages_dicts: List[Dict[str, Any]] = []
    for m in inp.messages:
        md = m.model_dump()
        md["content"] = normalize_content(md.get("content"))
        messages_dicts.append(md)

    sys_msgs = [m for m in messages_dicts if m.get("role") == "system"]
    chat_msgs = [m for m in messages_dicts if m.get("role") in ("user", "assistant")]

    tail_candidate = chat_msgs[-TAIL_MAX_CANDIDATE:] if len(chat_msgs) > TAIL_MAX_CANDIDATE else chat_msgs[:]
    rolling_summary = safe_str(st.get("summary", ""))

    head_msgs = pack_head(sys_msgs, budgets["head"])
    if rolling_summary:
        rolling_summary = trim_text_to_tokens(rolling_summary, budgets["summary"])

    prompt_msgs: List[Dict[str, str]] = []
    prompt_msgs.extend(head_msgs)

    if rolling_summary:
        prompt_msgs.append({"role": "system", "content": "ROLLING SUMMARY:\n" + rolling_summary})

    tail_msgs = keep_tail_within_budget(
        [{"role": m["role"], "content": safe_str(m.get("content"))} for m in tail_candidate],
        budgets["tail"]
    )
    prompt_msgs.extend(tail_msgs)

    summary_info = None
    if estimate_messages_tokens(prompt_msgs) > budgets["input"] and len(tail_msgs) > TAIL_KEEP:
        chunk = tail_msgs[:-TAIL_KEEP]
        tail_msgs = tail_msgs[-TAIL_KEEP:]
        rolling_summary, summary_info = update_summary(rolling_summary, chunk, budgets["summary"], req_id)

        prompt_msgs = []
        prompt_msgs.extend(head_msgs)
        prompt_msgs.append({"role": "system", "content": "ROLLING SUMMARY:\n" + rolling_summary})
        prompt_msgs.extend(tail_msgs)

        if estimate_messages_tokens(prompt_msgs) > budgets["input"]:
            tail_msgs = keep_tail_within_budget(tail_msgs, max(128, budgets["tail"] // 2))
            prompt_msgs = []
            prompt_msgs.extend(head_msgs)
            prompt_msgs.append({"role": "system", "content": "ROLLING SUMMARY:\n" + rolling_summary})
            prompt_msgs.extend(tail_msgs)

    prompt_tok_est = estimate_messages_tokens(prompt_msgs)

    # Main call
    t0 = time.time()
    try:
        j = litellm_chat("main", MAIN_MODEL, prompt_msgs, temperature=inp.temperature if inp.temperature is not None else 0.6, max_tokens=budgets["out"], req_id=req_id)
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text
        except Exception:
            pass
        logger.error("upstream_http_error", extra={"extra": {"req_id": req_id, "err": str(e), "body": _t(body, 800)}})
        raise HTTPException(status_code=502, detail=f"Upstream error: {e} {body[:500]}")

    elapsed = time.time() - t0

    answer = extract_content(j)
    if not answer:
        first_choice = (j.get("choices") or [{}])[0] if isinstance(j.get("choices"), list) else {}
        logger.warning("empty_main_content", extra={"extra": {"req_id": req_id, "top_keys": list(j.keys()), "choice_keys": list(first_choice.keys()) if isinstance(first_choice, dict) else str(type(first_choice))}})

    ans_tok_est = rough_tokens(answer)
    tok_s = _metrics_update("main", elapsed, ans_tok_est)

    st["summary"] = rolling_summary
    new_tail = tail_msgs + [{"role": "assistant", "content": answer}]
    st["tail"] = new_tail[-(TAIL_KEEP * 2):]

    total_elapsed = time.time() - t_req0

    logger.info("chat_completion", extra={"extra": {
        "req_id": req_id,
        "conv_key": conv_key,
        "ctx_eff": ctx_eff,
        "max_tokens_used": budgets["out"],
        "prompt_tok_est": prompt_tok_est,
        "answer_tok_est": ans_tok_est,
        "main_elapsed_ms": round(elapsed * 1000, 2),
        "main_tok_s_est": round(tok_s, 2),
        "total_elapsed_ms": round(total_elapsed * 1000, 2),
        "did_summarize": bool(summary_info),
    }})

    resp_obj = {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MAIN_MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens_est": prompt_tok_est,
            "completion_tokens_est": ans_tok_est,
            "ctx_eff": budgets["ctx"],
            "max_tokens_used": budgets["out"],
            "max_tokens_requested": inp.max_tokens if inp.max_tokens is not None else None,
            "timings_ms": {
                "main": round(elapsed * 1000, 2),
                "summary": (summary_info["elapsed_ms"] if summary_info else None),
                "total": round(total_elapsed * 1000, 2),
            },
            "tok_s_est": {
                "main": round(tok_s, 2),
                "summary": (summary_info["tok_s_est"] if summary_info else None),
            },
        },
    }

    headers = {}
    if DEBUG_ADD_HEADERS:
        headers = {
            "X-Req-Id": req_id,
            "X-CTX-Eff": str(ctx_eff),
            "X-MaxTokens-Req": str(inp.max_tokens) if inp.max_tokens is not None else "",
            "X-MaxTokens-Used": str(budgets["out"]),
            "X-PromptTok-Est": str(prompt_tok_est),
            "X-AnswerTok-Est": str(ans_tok_est),
            "X-MainTokS-Est": f"{tok_s:.2f}",
            "X-Summarized": "1" if summary_info else "0",
        }

    return JSONResponse(content=resp_obj, headers=headers)
