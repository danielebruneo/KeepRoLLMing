from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

import httpx
from rich import print_json

from .config import LOG_PAYLOAD_MAX_CHARS


def _ts() -> float:
    return time.time()


LOG_MODE_ENV = os.getenv("LOG_MODE", os.getenv("LOG_LEVEL", "DEBUG")).upper().strip()
LOG_MODE_CHOICES = {"DEBUG", "MEDIUM", "BASIC", "BASIC_PLAIN"}
LOG_MODE = LOG_MODE_ENV if LOG_MODE_ENV in LOG_MODE_CHOICES else "DEBUG"

LOG_SNIP_CHARS = int(os.getenv("LOG_SNIP_CHARS", "4000"))
BASIC_SNIP_CHARS = int(os.getenv("BASIC_SNIP_CHARS", "2000"))
LOG_STREAM_CHUNKS = os.getenv("LOG_STREAM_CHUNKS", "0").strip().lower() in {"1", "true", "yes", "on"}


def _snip_text_active(s: str | None, limit: int) -> str:
    if s is None:
        return ""
    return s if len(s) <= limit else (s[:limit] + f"... <snip {len(s)-limit} chars>")


def _snip_obj_active(obj: Any, limit: int) -> Any:
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


def get_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n".join(parts)
    return ""


def classify_messages(messages: Any) -> str:
    if not isinstance(messages, list):
        return "chat"

    system_texts = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            txt = get_text_content(m.get("content"))
            if txt:
                system_texts.append(txt)

    merged = "\n\n".join(system_texts)
    lowered = merged.lower()
    if "# `web_search`:" in merged or "execute immediately without preface" in lowered:
        return "web_search"
    if "don't reply to user, only handle memory" in lowered or "only handle memory" in lowered:
        return "memory"
    return "chat"


def summarize_request_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"kind": "unknown"}

    messages = payload.get("messages")
    summary: Dict[str, Any] = {
        "kind": classify_messages(messages),
        "model": payload.get("model"),
        "stream": bool(payload.get("stream", False)),
        "message_count": len(messages) if isinstance(messages, list) else None,
        "tool_count": len(payload.get("tools")) if isinstance(payload.get("tools"), list) else 0,
        "max_tokens": payload.get("max_tokens"),
    }

    last_user = extract_last_user_text(messages)
    if last_user:
        summary["last_user"] = _snip_text_active(last_user, BASIC_SNIP_CHARS)

    if isinstance(messages, list):
        summary["has_archived_context"] = any(
            isinstance(m, dict)
            and m.get("role") == "system"
            and "[ARCHIVED_COMPACT_CONTEXT]" in get_text_content(m.get("content"))
            for m in messages
        )
    return summary


def summarize_response_payload(data: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(data, dict):
        out["model"] = data.get("model")
        out["usage"] = data.get("usage")
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice0 = choices[0] if isinstance(choices[0], dict) else {}
            msg = choice0.get("message") if isinstance(choice0, dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    out["assistant_text"] = _snip_text_active(content, BASIC_SNIP_CHARS)
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    out["tool_calls"] = [
                        tc.get("function", {}).get("name")
                        for tc in tool_calls
                        if isinstance(tc, dict)
                    ]
            finish_reason = choice0.get("finish_reason") if isinstance(choice0, dict) else None
            if finish_reason:
                out["finish_reason"] = finish_reason
    return out


def _should_log(msg: str) -> bool:
    if LOG_MODE == "DEBUG":
        return True
    if LOG_MODE == "MEDIUM":
        return msg not in {"payload_in_full", "response_received"}
    return msg in {
        "startup",
        "http_in",
        "summary_needed",
        "summary_req",
        "summary_reply",
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
        "upstream_req_repacked",
        "http_out",
    }


def _format_plain(rec: Dict[str, Any]) -> str:
    msg = rec.get("msg")
    req_id = rec.get("req_id", "-")
    if msg == "conv_user":
        return f"[{req_id}] USER: {rec.get('text', '')}"
    if msg == "conv_assistant":
        return f"[{req_id}] ASSISTANT: {rec.get('text', '')}"
    if msg == "summary_needed":
        return (
            f"[{req_id}] SUMMARY_NEEDED model={rec.get('summary_model')} "
            f"prompt_tok={rec.get('prompt_tok_est')} threshold={rec.get('threshold')} "
            f"head={rec.get('head_n')} tail={rec.get('tail_n')} middle={rec.get('middle_count')}"
        )
    if msg == "summary_req":
        return (
            f"[{req_id}] SUMMARY_REQ model={rec.get('summary_model')} middle={rec.get('middle_count')} "
            f"transcript_chars={rec.get('transcript_chars')}"
        )
    if msg == "summary_reply":
        return (
            f"[{req_id}] SUMMARY_REPLY elapsed_ms={rec.get('elapsed_ms')} usage={rec.get('usage')} "
            f"summary={rec.get('summary_snip', '')}"
        )
    if msg == "upstream_req_repacked":
        kind = rec.get("kind", "chat")
        return (
            f"[{req_id}] CALL kind={kind} model={rec.get('model')} prompt_tokens={rec.get('prompt_tokens')} "
            f"did_summarize={rec.get('did_summarize')} archived={rec.get('has_archived_context')} "
            f"last_user={rec.get('last_user', '')}"
        )
    if msg == "http_out":
        return (
            f"[{req_id}] RESULT model={rec.get('model')} elapsed_ms={rec.get('elapsed_ms')} usage={rec.get('usage')} "
            f"assistant={rec.get('assistant_text', '')} tool_calls={rec.get('tool_calls')}"
        )
    if msg == "response_stream_reconstructed":
        return (
            f"[{req_id}] STREAM_RESULT model={rec.get('upstream_model')} elapsed_ms={rec.get('elapsed_ms')} "
            f"usage={rec.get('usage')} assistant={rec.get('assistant_text', '')}"
        )
    return f"[{req_id}] {msg}: {_snip_text_active(json.dumps(rec, ensure_ascii=False), BASIC_SNIP_CHARS)}"


def log(level: str, msg: str, **fields: Any) -> None:
    if not _should_log(msg):
        return
    rec = {"ts": _ts(), "level": level.upper(), "msg": msg, **fields}
    if LOG_MODE == "BASIC_PLAIN":
        print(_format_plain(rec))
        return
    print_json(data=rec)


MAX_BODY_CHARS = 4000


def _snip(s: str | None, limit: int = MAX_BODY_CHARS) -> str:
    if s is None:
        return ""
    return s if len(s) <= limit else (s[:limit] + f"... <snip {len(s)-limit} chars>")


def _is_json_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    ct = content_type.split(";", 1)[0].strip().lower()
    return ct == "application/json" or ct.endswith("+json")


async def log_request(req: httpx.Request) -> None:
    if LOG_MODE in {"BASIC", "BASIC_PLAIN"}:
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
    if LOG_MODE in {"BASIC", "BASIC_PLAIN"}:
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
    if not LOG_STREAM_CHUNKS:
        return
    if LOG_MODE not in {"DEBUG", "MEDIUM"}:
        return

    text = captured_bytes.decode("utf-8", errors="replace")
    events: list[Any] = []
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

        try:
            events.append(json.loads(payload))
        except Exception:
            events.append({"_raw": payload})

    body: Any
    if not events:
        body = {"_raw": _snip(text, MAX_BODY_CHARS)}
    elif len(events) == 1:
        body = events[0]
    else:
        body = events

    if LOG_MODE == "MEDIUM":
        body = _snip_obj_active(body, LOG_SNIP_CHARS)

    log(
        "DEBUG" if LOG_MODE == "DEBUG" else "INFO",
        "response_received",
        url=str(r.request.url),
        method=r.request.method,
        status=r.status_code,
        elapsed_ms=elapsed_ms,
        headers=dict(r.headers),
        body=body,
        note=f"streamed-chunk (bytes {len(captured_bytes)})",
    )
