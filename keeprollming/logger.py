from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
from rich import print_json

from .config import LOG_PAYLOAD_MAX_CHARS

# ----------------------------
# Logging (JSON lines)
# ----------------------------

def _ts() -> float:
    return time.time()

# -----------------------------------------------------------------------------
# Logging verbosity
#   DEBUG  : log everything (full JSON, no snipping)
#   MEDIUM : log requests + final responses (snipped), but not per-chunk SSE logs
#   BASIC  : minimal conversation-oriented logs (last user msg, assistant reply, tools/summary)
# -----------------------------------------------------------------------------
LOG_MODE_ENV = os.getenv("LOG_MODE", os.getenv("LOG_LEVEL", "DEBUG")).upper().strip()
LOG_MODE_CHOICES = {"DEBUG", "MEDIUM", "BASIC"}
LOG_MODE = LOG_MODE_ENV if LOG_MODE_ENV in LOG_MODE_CHOICES else "DEBUG"

# When snipping is needed (MEDIUM/BASIC), we DO NOT re-enable snip_json; we use a separate helper.
LOG_SNIP_CHARS = int(os.getenv("LOG_SNIP_CHARS", "4000"))
BASIC_SNIP_CHARS = int(os.getenv("BASIC_SNIP_CHARS", "2000"))

def _snip_text_active(s: str | None, limit: int) -> str:
    if s is None:
        return ""
    return s if len(s) <= limit else (s[:limit] + f"... <snip {len(s)-limit} chars>")

def _snip_obj_active(obj: Any, limit: int) -> Any:
    """Return obj unchanged if small; else return a JSON-safe preview object."""
    try:
        if obj is None:
            return None
        if isinstance(obj, str):
            return _snip_text_active(obj, limit)
        txt = json.dumps(obj, ensure_ascii=False)
        if len(txt) <= limit:
            return obj
        return {"_truncated": True, "preview": _snip_text_active(txt, limit)}
    except Exception:
        return {"_truncated": True, "preview": _snip_text_active(str(obj), limit)}

def extract_last_user_text(messages: Any) -> str | None:
    if not isinstance(messages, list):
        return None
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts = []
                for item in c:
                    if isinstance(item, dict):
                        t = item.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                if parts:
                    return "\n".join(parts)
            return None
    return None

def _should_log(msg: str) -> bool:
    if LOG_MODE == "DEBUG":
        return True
    if LOG_MODE == "MEDIUM":
        return msg not in {"payload_in_full", "response_received"}
    # BASIC
    return msg in {
        "startup",
        "http_in",
        "summary_needed",
        "repacked",
        "summary_failed_fallback_passthrough",
        "proxy_exception",
        "upstream_http_error",
        "upstream_http_error_stream",
        "upstream_stream_exception",
        "conv_user",
        "conv_assistant",
        "response_stream_reconstructed",
        "tool_call",
        "function_call",
    }

def log(level: str, msg: str, **fields: Any) -> None:
    if not _should_log(msg):
        return
    rec = {"ts": _ts(), "level": level.upper(), "msg": msg, **fields}
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
    if LOG_MODE == "BASIC":
        return

    content_type = req.headers.get("content-type", "")
    body_repr: Any = None

    if req.content:
        if "json" in content_type:
            try:
                body_repr = json.loads(req.content.decode())
            except Exception:
                body_repr = req.content.decode(errors="replace")
        else:
            body_repr = req.content.decode(errors="replace")

    if LOG_MODE == "MEDIUM":
        body_repr = _snip_obj_active(body_repr, LOG_SNIP_CHARS)

    log(
        "DEBUG" if LOG_MODE == "DEBUG" else "INFO",
        "request_sent",
        url=str(req.url),
        method=req.method,
        headers=dict(req.headers),
        body=body_repr,
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

async def log_response(r: httpx.Response, elapsed_ms: float | None = None) -> None:
    if LOG_MODE == "BASIC":
        return

    content_type = r.headers.get("content-type")
    body_repr: Any = None

    try:
        content = r.content or b""
    except httpx.ResponseNotRead:
        content = b""

    if content:
        if _is_json_content_type(content_type):
            try:
                body_repr = r.json()
            except Exception:
                body_repr = content.decode("utf-8", errors="replace")
        else:
            body_repr = content.decode("utf-8", errors="replace")

    if LOG_MODE == "MEDIUM":
        body_repr = _snip_obj_active(body_repr, LOG_SNIP_CHARS)

    log(
        "DEBUG" if LOG_MODE == "DEBUG" else "INFO",
        "response_received",
        url=str(r.request.url),
        method=r.request.method,
        status=r.status_code,
        elapsed_ms=elapsed_ms,
        headers=dict(r.headers),
        body=body_repr,
    )

async def log_streaming_response(
    r: httpx.Response,
    captured_bytes: bytes,
    *,
    elapsed_ms: float | None = None,
) -> None:
    # Only DEBUG logs raw streamed chunks
    if LOG_MODE != "DEBUG":
        return

    text = captured_bytes.decode("utf-8", errors="replace")

    # Parse SSE "data:" lines into JSON objects (or markers)
    events: list[Any] = []
    # One chunk can contain 0..N SSE events, separated by blank line
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue

        data_lines: list[str] = []
        for line in block.splitlines():
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if not data_lines:
            continue

        payload = "\n".join(data_lines).strip()
        if not payload:
            continue

        if payload == "[DONE]":
            events.append({"done": True})
            continue

        # Try JSON
        try:
            events.append(json.loads(payload))
        except Exception:
            # Fallback: keep raw payload (unescaped) but structured
            events.append({"_raw": payload})

    # If nothing parsed, fallback to raw text (still unescaped content)
    body: Any
    if not events:
        body = {"_raw": text}
    elif len(events) == 1:
        body = events[0]
    else:
        body = events

    log(
        "DEBUG",
        "response_received",
        url=str(r.request.url),
        method=r.request.method,
        status=r.status_code,
        elapsed_ms=elapsed_ms,
        headers=dict(r.headers),
        body=body,
        note=f"streamed-chunk (bytes {len(captured_bytes)})",
    )
