#!/usr/bin/env python3
"""
LibreChat Rolling-Summary Orchestrator (OpenAI-compatible) — v3 (requests-only, no openai/httpx)

This version avoids the OpenAI Python SDK entirely, fixing:
TypeError: Client.__init__() got an unexpected keyword argument 'proxies'

Features:
- Dynamic ctx from LM Studio: GET /api/v0/models (loaded_context_length)
- Respects LibreChat max_tokens; clamps if inconsistent
- Rolling summary using SUMMARY_MODEL via LiteLLM
- Debug logging + metrics + optional debug endpoints and response headers
- Token/sec estimates (heuristic len/4 tokens)
"""

from __future__ import annotations

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Tuple
import time
import hashlib
import os
import requests
import logging
import json
import uuid

LMSTUDIO_BASE = os.getenv("LMSTUDIO_BASE", "http://127.0.0.1:1234")
LITELLM_BASE = os.getenv("LITELLM_BASE", "http://127.0.0.1:4000/v1")

SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "local/summary")
MAIN_MODEL = os.getenv("MAIN_MODEL", "local/main")

LM_MAIN_ID = os.getenv("LM_MAIN_ID", "qwen2.5-3b-instruct")
LM_SUMMARY_ID = os.getenv("LM_SUMMARY_ID", "qwen2.5-1.5b-instruct")

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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_JSON = os.getenv("LOG_JSON", "0") == "1"
DEBUG_ENABLE_ENDPOINTS = os.getenv("DEBUG_ENABLE_ENDPOINTS", "0") == "1"
DEBUG_ADD_HEADERS = os.getenv("DEBUG_ADD_HEADERS", "1") == "1"
EMA_ALPHA = float(os.getenv("EMA_ALPHA", "0.2"))

LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "x")

HEAD_ANCHORS = os.getenv("HEAD_ANCHORS", "").strip() or (
    "HEAD ANCHORS:\n"
    "- Sei un assistente tecnico pratico per stack AI locale (LibreChat/LM Studio/LiteLLM).\n"
    "- L'orchestrator gestisce rolling summary + packing nel contesto.\n"
    "- Risposte operative, passi chiari, niente fuffa.\n"
)

MAIN_SYSTEM = os.getenv("MAIN_SYSTEM", "").strip() or (
    "Sei un assistente tecnico pratico e concreto. "
    "Aiuta l'utente a costruire e mantenere una pipeline locale con LibreChat + LM Studio + LiteLLM, "
    "gestendo contesto, RAG e rolling summary."
)

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

logger = logging.getLogger("orchestrator")
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

app = FastAPI(title="LibreChat Orchestrator (requests-only)", version="3.0")

STATE: Dict[str, Dict[str, Any]] = {}
CTX_CACHE: Dict[str, Tuple[int, float]] = {}

METRICS: Dict[str, Dict[str, Any]] = {
    "summary": {"calls": 0, "avg_ms": 0.0, "ema_tok_s": 0.0, "avg_tok_s": 0.0, "tok_sum": 0, "sec_sum": 0.0},
    "main":    {"calls": 0, "avg_ms": 0.0, "ema_tok_s": 0.0, "avg_tok_s": 0.0, "tok_sum": 0, "sec_sum": 0.0},
    "ctx":     {"reads": 0, "avg_ms": 0.0, "last_ctx_main": None, "last_ctx_summary": None, "last_ctx_eff": None},
}

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
        if m.get("role") == "user":
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
        logger.warning("LM Studio ctx read failed; using fallback", extra={"extra": {"err": str(e), "model_id": model_id}})
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
    if requested_max_tokens is None:
        out = min(DEFAULT_OUT_CAP, int(ctx_eff * DEFAULT_OUT_FRAC))
    else:
        out = int(requested_max_tokens)
    max_out_allowed = max(MIN_OUT, ctx_eff - margin - MIN_INPUT)
    out = clamp(out, MIN_OUT, max_out_allowed)
    input_budget = max(MIN_INPUT, ctx_eff - margin - out)
    head = int(input_budget * HEAD_FRAC)
    summary = int(input_budget * SUMMARY_FRAC)
    tail = int(input_budget * TAIL_FRAC)
    rag = max(0, input_budget - head - summary - tail)
    return {"ctx": ctx_eff, "margin": margin, "out": out, "input": input_budget,
            "head": head, "summary": summary, "tail": tail, "rag": rag}

def trim_text_to_tokens(text: str, token_budget: int) -> str:
    if rough_tokens(text) <= token_budget:
        return text
    return text[: max(0, token_budget * 4) ]

def pack_head(system_messages: List[Dict[str, str]], head_budget_tokens: int) -> List[Dict[str, str]]:
    head_msgs: List[Dict[str, str]] = [{"role": "system", "content": MAIN_SYSTEM}]
    sys_texts = []
    for m in system_messages:
        c = safe_str(m.get("content"))
        if c:
            sys_texts.append(c)
    combined = ("\n\n".join(sys_texts))[:HARD_HEAD_CHAR_CAP]
    combined = trim_text_to_tokens(combined, max(0, head_budget_tokens - rough_tokens(HEAD_ANCHORS) - 80))
    if combined.strip():
        head_msgs.append({"role": "system", "content": combined})
    anchors = trim_text_to_tokens(HEAD_ANCHORS, max(32, head_budget_tokens // 3))
    head_msgs.append({"role": "system", "content": anchors})
    while estimate_messages_tokens(head_msgs) > head_budget_tokens and len(head_msgs) >= 2:
        last = head_msgs[-1]["content"]
        head_msgs[-1]["content"] = trim_text_to_tokens(last, max(16, rough_tokens(last) - 32))
        if rough_tokens(head_msgs[-1]["content"]) <= 16:
            if len(head_msgs) > 2:
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

def litellm_chat(model: str, messages: List[Dict[str, str]], temperature: float, max_tokens: int, req_id: str) -> Dict[str, Any]:
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
        "X-Request-Id": req_id,
    }
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
    r = requests.post(url, headers=headers, json=body, timeout=600)
    r.raise_for_status()
    return r.json()

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
    j = litellm_chat(SUMMARY_MODEL, messages, temperature=0.2, max_tokens=min(950, max(128, summary_budget_tokens + 64)), req_id=req_id)
    elapsed = time.time() - t0
    out_text = (((j.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    out_text = trim_text_to_tokens(out_text, summary_budget_tokens)
    out_tok = rough_tokens(out_text)
    tok_s = _metrics_update("summary", elapsed, out_tok)
    info = {"elapsed_ms": round(elapsed * 1000, 2), "out_tok_est": out_tok, "tok_s_est": round(tok_s, 2)}
    logger.info("summary_call", extra={"extra": {"req_id": req_id, **info}})
    return out_text, info

class ChatCompletionsIn(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
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

@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    logger.info("chat_completition")
    payload = await req.json()
    try:
        inp = ChatCompletionsIn(**payload)
    except Exception as e:
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

    sys_msgs = [m for m in inp.messages if m.get("role") == "system"]
    chat_msgs = [m for m in inp.messages if m.get("role") in ("user", "assistant")]

    tail_candidate = chat_msgs[-TAIL_MAX_CANDIDATE:] if len(chat_msgs) > TAIL_MAX_CANDIDATE else chat_msgs[:]
    rolling_summary = safe_str(st.get("summary", ""))

    head_msgs = pack_head(sys_msgs, budgets["head"])
    if rolling_summary:
        rolling_summary = trim_text_to_tokens(rolling_summary, budgets["summary"])

    prompt_msgs: List[Dict[str, str]] = list(head_msgs)
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
        prompt_msgs = list(head_msgs)
        prompt_msgs.append({"role": "system", "content": "ROLLING SUMMARY:\n" + rolling_summary})
        prompt_msgs.extend(tail_msgs)
        if estimate_messages_tokens(prompt_msgs) > budgets["input"]:
            tail_msgs = keep_tail_within_budget(tail_msgs, max(128, budgets["tail"] // 2))
            prompt_msgs = list(head_msgs)
            prompt_msgs.append({"role": "system", "content": "ROLLING SUMMARY:\n" + rolling_summary})
            prompt_msgs.extend(tail_msgs)

    prompt_tok_est = estimate_messages_tokens(prompt_msgs)

    t0 = time.time()
    try:
        j = litellm_chat(
            MAIN_MODEL,
            prompt_msgs,
            temperature=inp.temperature if inp.temperature is not None else 0.6,
            max_tokens=budgets["out"],
            req_id=req_id
        )
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"Upstream LiteLLM error: {e} {body[:500]}")
    elapsed = time.time() - t0

    answer = (((j.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    ans_tok_est = rough_tokens(answer)
    tok_s = _metrics_update("main", elapsed, ans_tok_est)

    st["summary"] = rolling_summary
    new_tail = tail_msgs + [{"role": "assistant", "content": answer}]
    st["tail"] = new_tail[-(TAIL_KEEP * 2):]

    total_elapsed = time.time() - t_req0

    logger.info("chat_completion", extra={"extra": {
        "req_id": req_id,
        "conv_key": conv_key,
        "ctx_main": ctx_main,
        "ctx_sum": ctx_sum,
        "ctx_eff": ctx_eff,
        "max_tokens_req": inp.max_tokens,
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
