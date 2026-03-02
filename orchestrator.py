#!/usr/bin/env python3
"""
LibreChat Rolling-Summary Orchestrator — v3.6

Changes vs v3.5:
- Removed proactive summary trigger (NO identity/N-messages trigger). Summary happens ONLY on overflow.
- Streaming: still proxies SSE and captures assistant output to update state.
- Adds detailed token accounting (estimated) for current request:
  - head_tok_est, rolling_summary_tok_est, tail_tok_est, prompt_tok_est, input_budget, out_budget, ctx_eff
- Adds tok/s metrics:
  - main_tok_s_est derived from elapsed + output tokens estimate
  - main_tok_s_backend if stream_options.include_usage yields usage in SSE (best-effort)
  - summary_tok_s_est derived from elapsed + output tokens estimate
  - summary_tok_s_backend if usage returned by backend (best-effort)
- Clear logs for summary:
  - summary_trigger (why), summary_call (elapsed/tok), summary_applied (new lengths)
"""

from __future__ import annotations
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Optional, Tuple, Iterator
import time, hashlib, os, requests, logging, json, uuid

LMSTUDIO_BASE = os.getenv("LMSTUDIO_BASE", "http://127.0.0.1:1234")
LITELLM_BASE = os.getenv("LITELLM_BASE", "http://127.0.0.1:4000/v1")  # must include /v1

SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "local/summary")
MAIN_MODEL = os.getenv("MAIN_MODEL", "local/main")

LM_MAIN_ID = os.getenv("LM_MAIN_ID", "qwen2.5-3b-instruct")
LM_SUMMARY_ID = os.getenv("LM_SUMMARY_ID", "qwen2.5-1.5b-instruct")

LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "x")

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

TAIL_KEEP = int(os.getenv("TAIL_KEEP", "2"))
TAIL_MAX_CANDIDATE = int(os.getenv("TAIL_MAX_CANDIDATE", "14"))
HARD_HEAD_CHAR_CAP = int(os.getenv("HARD_HEAD_CHAR_CAP", "6000"))

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
        return json.dumps(base, ensure_ascii=False, default=str)

handler.setFormatter(JsonFormatter() if LOG_JSON else logging.Formatter("%(asctime)s %(levelname)s - %(message)s"))
logger.handlers = [handler]

def _t(text: str, n: int) -> str:
    if text is None:
        return ""
    return text if len(text) <= n else text[:n] + "…"

# -----------------------
# App + State
# -----------------------
app = FastAPI(title="LibreChat Orchestrator", version="3.6")

STATE: Dict[str, Dict[str, Any]] = {}
CTX_CACHE: Dict[str, Tuple[int, float]] = {}
LAST_UPSTREAM: Dict[str, Dict[str, Any]] = {"main": {}, "summary": {}}

METRICS: Dict[str, Dict[str, Any]] = {
    "summary": {"calls": 0, "avg_ms": 0.0, "ema_tok_s": 0.0, "avg_tok_s": 0.0, "tok_sum": 0, "sec_sum": 0.0, "last_tok_s_backend": None},
    "main":    {"calls": 0, "avg_ms": 0.0, "ema_tok_s": 0.0, "avg_tok_s": 0.0, "tok_sum": 0, "sec_sum": 0.0, "last_tok_s_backend": None},
    "ctx":     {"reads": 0, "avg_ms": 0.0, "last_ctx_main": None, "last_ctx_summary": None, "last_ctx_eff": None},
    "stream":  {"sessions": 0, "completed": 0, "avg_ms": 0.0, "avg_chars": 0.0, "avg_out_tok_est": 0.0},
}

@app.middleware("http")
async def log_requests(request: Request, call_next):
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.time()
    logger.info("http_in", extra={"extra": {
        "req_id": req_id, "method": request.method, "path": request.url.path,
        "client": (request.client.host if request.client else None),
        "ct": request.headers.get("content-type"),
        "ua": request.headers.get("user-agent"),
    }})
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
# Normalization helpers
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
                parts.append(item); continue
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"])); continue
                for k in ("text", "content", "value"):
                    if k in item and item[k] is not None:
                        parts.append(str(item[k])); break
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
    return "asd"
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

def pack_head(system_messages: List[Dict[str, str]], head_budget_tokens: int) -> Tuple[List[Dict[str, str]], int]:
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
    return head_msgs, estimate_messages_tokens(head_msgs)

def keep_tail_within_budget(chat_messages: List[Dict[str, str]], tail_budget_tokens: int) -> Tuple[List[Dict[str, str]], int]:
    msgs = list(chat_messages)
    if not msgs:
        return msgs, 0
    while estimate_messages_tokens(msgs) > tail_budget_tokens and len(msgs) > 2:
        msgs.pop(0)
    if estimate_messages_tokens(msgs) > tail_budget_tokens and msgs:
        msgs[0]["content"] = trim_text_to_tokens(safe_str(msgs[0].get("content")), max(16, tail_budget_tokens // 2))
    return msgs, estimate_messages_tokens(msgs)

def upstream_chat(kind: str, model: str, messages: List[Dict[str, str]], temperature: float, max_tokens: int, req_id: str, stream: bool, stream_options: Optional[dict]) -> requests.Response:
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_API_KEY}",
        "Content-Type": "application/json",
        "X-Request-Id": req_id,
        "Accept": "text/event-stream" if stream else "application/json",
    }
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": bool(stream),
    }
    if stream and stream_options:
        body["stream_options"] = stream_options

    if DEBUG_UPSTREAM:
        logger.info("up_req", extra={"extra": {"req_id": req_id, "kind": kind, "url": url, "body": _t(json.dumps(body, ensure_ascii=False), DEBUG_UPSTREAM_BODY_CHARS)}})

    r = requests.post(url, headers=headers, json=body, timeout=600, stream=stream)
    if not stream:
        txt = r.text or ""
        LAST_UPSTREAM[kind] = {"ts": time.time(), "req_id": req_id, "url": url, "status": r.status_code,
                              "req_body": body if DEBUG_ENABLE_ENDPOINTS else None,
                              "res_text": _t(txt, 8000)}
    else:
        LAST_UPSTREAM[kind] = {"ts": time.time(), "req_id": req_id, "url": url, "status": r.status_code,
                              "req_body": body if DEBUG_ENABLE_ENDPOINTS else None,
                              "res_text": "(streaming)"}
    r.raise_for_status()
    return r

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

def extract_usage(j: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    u = j.get("usage")
    return u if isinstance(u, dict) else None

def update_summary(prev_summary: str, chunk: List[Dict[str, str]], summary_budget_tokens: int, req_id: str) -> Tuple[str, Dict[str, Any]]:
    logger.info("summary_trigger", extra={"extra": {"req_id": req_id, "reason": "overflow", "chunk_msgs": len(chunk)}})
    chunk_text = "\n".join(f"{m.get('role','').upper()}: {safe_str(m.get('content'))}" for m in chunk)
    user_payload = (
        "MEMORIA PRECEDENTE:\n"
        f"{prev_summary or '(vuota)'}\n\n"
        "NUOVI MESSAGGI DA INTEGRARE:\n"
        f"{chunk_text}"
    )
    messages = [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": user_payload}]
    t0 = time.time()
    r = upstream_chat("summary", SUMMARY_MODEL, messages, temperature=0.2,
                     max_tokens=min(950, max(128, summary_budget_tokens + 64)),
                     req_id=req_id, stream=False, stream_options=None)
    j = r.json()
    elapsed = time.time() - t0
    out_text = extract_content(j) or ""
    out_text = trim_text_to_tokens(out_text, summary_budget_tokens)
    out_tok = rough_tokens(out_text)
    tok_s_est = _metrics_update("summary", elapsed, out_tok)

    usage = extract_usage(j)
    tok_s_backend = None
    if usage and usage.get("completion_tokens") and elapsed > 0:
        try:
            tok_s_backend = float(usage["completion_tokens"]) / float(elapsed)
            METRICS["summary"]["last_tok_s_backend"] = tok_s_backend
        except Exception:
            tok_s_backend = None

    info = {
        "elapsed_ms": round(elapsed * 1000, 2),
        "out_tok_est": out_tok,
        "tok_s_est": round(tok_s_est, 2),
        "tok_s_backend": round(tok_s_backend, 2) if tok_s_backend is not None else None,
        "usage": usage,
    }
    logger.info("summary_call", extra={"extra": {"req_id": req_id, **info}})
    return out_text, info

# -----------------------
# Schemas (allow extra)
# -----------------------
class MessageIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = ""

class ChatCompletionsIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    user: Optional[str] = None
    messages: List[MessageIn]
    temperature: Optional[float] = 0.6
    max_tokens: Optional[int] = Field(default=None)
    stream: Optional[bool] = False
    stream_options: Optional[dict] = None

@app.get("/v1/models")
def list_models():
    return {"object": "list", "data": [{"id": MAIN_MODEL, "object": "model"}, {"id": SUMMARY_MODEL, "object": "model"}]}

@app.get("/debug/metrics")
def debug_metrics():
    if not DEBUG_ENABLE_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not found")
    return METRICS

@app.get("/debug/last_upstream/{kind}")
def debug_last_upstream(kind: str):
    if not DEBUG_ENABLE_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Not found")
    return LAST_UPSTREAM.get(kind) or {}

@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    payload = await req.json()
    payload = normalize_messages(payload)

    print(">>>IN:")
    logger.info("payload_in", extra={"extra": {"top_keys": list(payload.keys()), "payload_snip": _t(json.dumps(payload, ensure_ascii=False), 2500)}})

    try:
        inp = ChatCompletionsIn(**payload)
    except Exception as e:
        logger.error("invalid_payload", extra={"extra": {"err": str(e), "top_keys": list(payload.keys())}})
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

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

    # normalize to dict list
    messages_dicts: List[Dict[str, Any]] = []
    for m in inp.messages:
        md = m.model_dump()
        md["content"] = normalize_content(md.get("content"))
        messages_dicts.append(md)

    sys_msgs = [m for m in messages_dicts if m.get("role") == "system"]
    chat_msgs = [m for m in messages_dicts if m.get("role") in ("user", "assistant")]

    tail_candidate = chat_msgs[-TAIL_MAX_CANDIDATE:] if len(chat_msgs) > TAIL_MAX_CANDIDATE else chat_msgs[:]
    rolling_summary = safe_str(st.get("summary", ""))

    head_msgs, head_tok_est = pack_head(sys_msgs, budgets["head"])
    rolling_summary_tok_est = rough_tokens(rolling_summary) if rolling_summary else 0
    if rolling_summary:
        rolling_summary = trim_text_to_tokens(rolling_summary, budgets["summary"])
        rolling_summary_tok_est = rough_tokens(rolling_summary)

    prompt_msgs: List[Dict[str, str]] = []
    prompt_msgs.extend(head_msgs)
    if rolling_summary:
        prompt_msgs.append({"role": "system", "content": "ROLLING SUMMARY:\n" + rolling_summary})

    tail_msgs, tail_tok_est = keep_tail_within_budget(
        [{"role": m["role"], "content": safe_str(m.get("content"))} for m in tail_candidate],
        budgets["tail"]
    )
    prompt_msgs.extend(tail_msgs)

    # Overflow-only summary trigger
    did_summarize = False
    summary_info = None
    prompt_msgs = messages_dicts
    print("PROMPT MESSAGES:")
    print(prompt_msgs)
    print("ESTI^ MSG TKNS:")
    print(estimate_messages_tokens(prompt_msgs))
    print("BUDGET INPUT:")
    print(budgets["input"])
    print("LEN TAIL:")
    print(len(tail_msgs))
    if estimate_messages_tokens(prompt_msgs) > budgets["input"] and len(tail_msgs) > TAIL_KEEP:
        chunk = tail_msgs[:-TAIL_KEEP]
        tail_msgs = tail_msgs[-TAIL_KEEP:]
        rolling_summary, summary_info = update_summary(rolling_summary, chunk, budgets["summary"], req_id)
        did_summarize = True
        rolling_summary_tok_est = rough_tokens(rolling_summary)

        prompt_msgs = []
        prompt_msgs.extend(head_msgs)
        prompt_msgs.append({"role": "system", "content": "ROLLING SUMMARY:\n" + rolling_summary})
        prompt_msgs.extend(tail_msgs)
        tail_tok_est = estimate_messages_tokens(tail_msgs)
        
        print(">>>SUMMARY<<<")
        logger.info("summary_applied", extra={"extra": {"req_id": req_id, "new_summary_tok_est": rolling_summary_tok_est, "new_tail_tok_est": tail_tok_est}})

    prompt_tok_est = estimate_messages_tokens(prompt_msgs)

    logger.info("budget", extra={"extra": {
        "req_id": req_id, "conv_key": conv_key,
        "ctx_eff": ctx_eff, "input_budget": budgets["input"], "out_budget": budgets["out"], "margin": budgets["margin"],
        "head_tok_est": head_tok_est, "rolling_summary_tok_est": rolling_summary_tok_est, "tail_tok_est": tail_tok_est,
        "prompt_tok_est": prompt_tok_est,
        "did_summarize": bool(did_summarize),
    }})

    stream = bool(inp.stream)

    # MAIN call
    t0 = time.time()
    try:
        r = upstream_chat(
            "main",
            MAIN_MODEL,
            prompt_msgs,
            temperature=inp.temperature if inp.temperature is not None else 0.6,
            max_tokens=budgets["out"],
            req_id=req_id,
            stream=stream,
            stream_options=inp.stream_options,
        )
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text
        except Exception:
            pass
        logger.error("upstream_http_error", extra={"extra": {"req_id": req_id, "err": str(e), "body": _t(body, 800)}})
        raise HTTPException(status_code=502, detail=f"Upstream error: {e} {body[:500]}")

    if stream:
        METRICS["stream"]["sessions"] += 1
        buf: List[str] = []
        usage_final: Optional[Dict[str, Any]] = None
        start = time.time()

        def gen() -> Iterator[bytes]:
            nonlocal buf, usage_final
            for raw in r.iter_lines(decode_unicode=False, delimiter=b"\n"):
                if not raw:
                    yield b"\n"
                    continue
                yield raw + b"\n"

                if raw.startswith(b"data:"):
                    data = raw[5:].strip()
                    if data == b"[DONE]":
                        break
                    try:
                        j = json.loads(data.decode("utf-8", errors="ignore"))
                        # capture deltas
                        ch = (j.get("choices") or [{}])[0]
                        delta = ch.get("delta") or {}
                        piece = delta.get("content")
                        if isinstance(piece, str) and piece:
                            buf.append(piece)
                        # capture usage if present (include_usage)
                        if isinstance(j.get("usage"), dict):
                            usage_final = j["usage"]
                    except Exception:
                        pass

            answer = "".join(buf).strip()
            print(">>>ANSWER:")
            print(answer)
            # update state on stream completion
            if answer:
                st["summary"] = rolling_summary
                new_tail = tail_msgs + [{"role": "assistant", "content": answer}]
                st["tail"] = new_tail[-(TAIL_KEEP * 2):]

            elapsed = time.time() - start
            out_tok_est = rough_tokens(answer) if answer else 0
            tok_s_est = (out_tok_est / elapsed) if elapsed > 0 else 0.0
            _metrics_update("main", elapsed, out_tok_est)

            tok_s_backend = None
            if usage_final and usage_final.get("completion_tokens") and elapsed > 0:
                try:
                    tok_s_backend = float(usage_final["completion_tokens"]) / float(elapsed)
                    METRICS["main"]["last_tok_s_backend"] = tok_s_backend
                except Exception:
                    tok_s_backend = None

            METRICS["stream"]["completed"] += 1
            METRICS["stream"]["avg_ms"] = (METRICS["stream"]["avg_ms"] * (METRICS["stream"]["completed"] - 1) + elapsed * 1000.0) / METRICS["stream"]["completed"]
            METRICS["stream"]["avg_chars"] = (METRICS["stream"]["avg_chars"] * (METRICS["stream"]["completed"] - 1) + len(answer)) / METRICS["stream"]["completed"]
            METRICS["stream"]["avg_out_tok_est"] = (METRICS["stream"]["avg_out_tok_est"] * (METRICS["stream"]["completed"] - 1) + out_tok_est) / METRICS["stream"]["completed"]

            logger.info("main_stream_done", extra={"extra": {
                "req_id": req_id, "conv_key": conv_key,
                "elapsed_ms": round(elapsed * 1000, 2),
                "out_tok_est": out_tok_est,
                "tok_s_est": round(tok_s_est, 2),
                "tok_s_backend": round(tok_s_backend, 2) if tok_s_backend is not None else None,
                "usage": usage_final,
                "did_summarize": bool(did_summarize),
            }})

        logger.info("chat_stream_proxy", extra={"extra": {
            "req_id": req_id, "conv_key": conv_key, "ctx_eff": ctx_eff,
            "prompt_tok_est": prompt_tok_est, "max_tokens_used": budgets["out"],
            "total_elapsed_ms": round((time.time() - t_req0) * 1000.0, 2),
            "did_summarize": bool(did_summarize),
        }})
        headers = {"X-Req-Id": req_id, "X-CTX-Eff": str(ctx_eff)} if DEBUG_ADD_HEADERS else {}
        return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)

    # Non-stream:
    j = r.json()
    elapsed = time.time() - t0
    answer = extract_content(j)
    ans_tok_est = rough_tokens(answer)
    tok_s_est = _metrics_update("main", elapsed, ans_tok_est)

    usage = extract_usage(j)
    tok_s_backend = None
    if usage and usage.get("completion_tokens") and elapsed > 0:
        try:
            tok_s_backend = float(usage["completion_tokens"]) / float(elapsed)
            METRICS["main"]["last_tok_s_backend"] = tok_s_backend
        except Exception:
            tok_s_backend = None

    st["summary"] = rolling_summary
    new_tail = tail_msgs + [{"role": "assistant", "content": answer}]
    st["tail"] = new_tail[-(TAIL_KEEP * 2):]

    logger.info("main_completion", extra={"extra": {
        "req_id": req_id, "conv_key": conv_key,
        "ctx_eff": ctx_eff, "max_tokens_used": budgets["out"],
        "prompt_tok_est": prompt_tok_est, "answer_tok_est": ans_tok_est,
        "elapsed_ms": round(elapsed * 1000, 2),
        "tok_s_est": round(tok_s_est, 2),
        "tok_s_backend": round(tok_s_backend, 2) if tok_s_backend is not None else None,
        "usage": usage,
        "did_summarize": bool(did_summarize),
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
            "tok_s_est": round(tok_s_est, 2),
            "tok_s_backend": round(tok_s_backend, 2) if tok_s_backend is not None else None,
        },
    }
    return JSONResponse(content=resp_obj)
