from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import time
import textwrap
from datetime import datetime
from typing import Any, Dict

import httpx
from rich import print_json

from .config import LOG_PAYLOAD_MAX_CHARS


def _ts() -> float:
    return time.time()


LOG_MODE_ENV = os.getenv("LOG_MODE", os.getenv("LOG_LEVEL", "DEBUG")).upper().strip()
LOG_MODE_CHOICES = {"DEBUG", "MEDIUM", "BASIC", "BASIC_PLAIN"}
LOG_MODE = LOG_MODE_ENV if LOG_MODE_ENV in LOG_MODE_CHOICES else "DEBUG"

LOG_SNIP_CHARS = int(os.getenv("LOG_SNIP_CHARS", "4000"))
BASIC_SNIP_CHARS = int(os.getenv("BASIC_SNIP_CHARS", "0"))
LOG_STREAM_CHUNKS = os.getenv("LOG_STREAM_CHUNKS", "0").strip().lower() in {"1", "true", "yes", "on"}
LOG_PLAIN_COLORS = os.getenv("LOG_PLAIN_COLORS", "1").strip().lower() in {"1", "true", "yes", "on"}
LOG_PLAIN_WRAP_WIDTH = int(os.getenv("LOG_PLAIN_WRAP_WIDTH", "80"))

_PLAIN_LAST_REQ_ID: str | None = None
_PLAIN_CLOSED_REQ_IDS: set[str] = set()

ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"
ANSI_CYAN = "\x1b[36m"
ANSI_GREEN = "\x1b[32m"
ANSI_MAGENTA = "\x1b[35m"
ANSI_YELLOW = "\x1b[33m"
ANSI_BLUE = "\x1b[34m"
ANSI_RED = "\x1b[31m"
ANSI_GRAY = "\x1b[90m"


def _c(text: str, *codes: str) -> str:
    if not LOG_PLAIN_COLORS or not codes:
        return text
    return "".join(codes) + text + ANSI_RESET


def _snip_text_active(s: str | None, limit: int) -> str:
    if s is None:
        return ""
    if limit <= 0:
        return s
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
        "summary_cache_lookup",
        "summary_cache_hit",
        "summary_cache_miss",
        "summary_cache_save",
        "summary_consolidate",
        "repacked",
        "summary_failed_fallback_passthrough",
        "summary_bypassed",
        "max_tokens_clamped",
        "proxy_exception",
        "upstream_http_error",
        "upstream_http_error_stream",
        "upstream_stream_exception",
        "conv_user",
        "conv_assistant",
        "response_stream_reconstructed",
        "stream_progress",
        "tool_call",
        "function_call",
        "upstream_req_repacked",
        "http_out",
    }


def _highlight_speaker_chunk(line: str) -> str:
    for speaker, color in (("AI:", ANSI_GREEN), ("Human:", ANSI_CYAN), ("USER:", ANSI_CYAN), ("ASSISTANT:", ANSI_GREEN)):
        if line.startswith(speaker):
            return _c(speaker, ANSI_BOLD, color) + line[len(speaker):]
    return line


def _wrap_plain_line(line: str, *, available_width: int) -> list[str]:
    if available_width <= 0 or LOG_PLAIN_WRAP_WIDTH <= 0:
        return [line]
    if len(_strip_ansi(line)) <= available_width:
        return [line]

    speaker_prefix = ""
    m = re.match(r"^(AI:|Human:|USER:|ASSISTANT:)(\s*)", line)
    if m:
        speaker_prefix = " " * len(_strip_ansi(m.group(0)))

    wrapped = textwrap.wrap(
        line,
        width=available_width,
        replace_whitespace=False,
        drop_whitespace=False,
        break_long_words=False,
        break_on_hyphens=False,
        subsequent_indent=speaker_prefix,
    )
    return wrapped or [line]


def _indent_block(text: str | None, prefix: str = "│   ") -> str:
    if not text:
        return prefix.rstrip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    available_width = max(0, LOG_PLAIN_WRAP_WIDTH - len(_strip_ansi(prefix)))
    out: list[str] = []
    for line in lines:
        for wrapped in _wrap_plain_line(line, available_width=available_width):
            out.append(prefix + _highlight_speaker_chunk(wrapped))
    return "\n".join(out)


def _fmt_meta_item(key: str, value: Any) -> str:
    return f"{_c(key, ANSI_DIM, ANSI_CYAN)}={_c(str(value), ANSI_BOLD, ANSI_YELLOW)}"


def _fmt_meta(**kwargs: Any) -> str:
    return " ".join(_fmt_meta_item(k, v) for k, v in kwargs.items() if v is not None)


def _normalize_summary_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, str):
                value = parsed
        except Exception:
            pass
        return value.replace("\r\n", "\n").replace("\r", "\n")
    return str(value)

def _fmt_usage(usage: Any) -> str:
    if not isinstance(usage, dict):
        return "-"
    p = usage.get("prompt_tokens")
    c = usage.get("completion_tokens")
    t = usage.get("total_tokens")
    parts = []
    if p is not None:
        parts.append(f"prompt={p}")
    if c is not None:
        parts.append(f"completion={c}")
    if t is not None:
        parts.append(f"total={t}")
    return ", ".join(parts) if parts else "-"


def _plain_header(req_id: str) -> str:
    return _c(f"┌─ REQUEST {req_id}", ANSI_BOLD, ANSI_CYAN)


def _plain_footer(req_id: str) -> str:
    return _c(f"└─ END {req_id}", ANSI_DIM, ANSI_GRAY)


def _open_plain_request_if_needed(req_id: str) -> str:
    global _PLAIN_LAST_REQ_ID
    if not req_id or req_id == "-":
        return ""

    out: list[str] = []
    if _PLAIN_LAST_REQ_ID and _PLAIN_LAST_REQ_ID != req_id and _PLAIN_LAST_REQ_ID not in _PLAIN_CLOSED_REQ_IDS:
        out.append(_plain_footer(_PLAIN_LAST_REQ_ID))
        _PLAIN_CLOSED_REQ_IDS.add(_PLAIN_LAST_REQ_ID)
        out.append("")
    if _PLAIN_LAST_REQ_ID != req_id:
        out.append(_plain_header(req_id))
        _PLAIN_LAST_REQ_ID = req_id
    return "\n".join(out)


def _maybe_close_plain_request(msg: str, req_id: str) -> str:
    global _PLAIN_LAST_REQ_ID
    if not req_id or req_id == "-":
        return ""
    terminal_msgs = {
        "http_out",
        "response_stream_reconstructed",
        "proxy_exception",
        "upstream_http_error",
        "upstream_http_error_stream",
        "upstream_stream_exception",
    }
    if msg not in terminal_msgs or req_id in _PLAIN_CLOSED_REQ_IDS:
        return ""
    _PLAIN_CLOSED_REQ_IDS.add(req_id)
    if _PLAIN_LAST_REQ_ID == req_id:
        _PLAIN_LAST_REQ_ID = None
    return _plain_footer(req_id)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _format_plain(rec: Dict[str, Any]) -> str:
    msg = rec.get("msg")
    req_id = rec.get("req_id", "-")
    parts: list[str] = []
    header = _open_plain_request_if_needed(req_id)
    if header:
        parts.append(header)

    def add_section(title: str, color: str, body: str | None = None, meta: str | None = None) -> None:
        title_txt = _c(f"│ {title}", ANSI_BOLD, color)
        if meta:
            title_txt += " " + meta
        plain_title = _strip_ansi(title_txt)
        if LOG_PLAIN_WRAP_WIDTH > 0 and len(plain_title) > LOG_PLAIN_WRAP_WIDTH:
            wrapped = textwrap.wrap(
                title_txt,
                width=LOG_PLAIN_WRAP_WIDTH,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=False,
                break_on_hyphens=False,
                subsequent_indent="│   ",
            )
            parts.extend(wrapped or [title_txt])
        else:
            parts.append(title_txt)
        if body is not None:
            parts.append(_indent_block(body))

    if msg == "conv_user":
        add_section("USER", ANSI_CYAN, rec.get("text", ""))
    elif msg == "conv_assistant":
        add_section("ASSISTANT", ANSI_GREEN, rec.get("text", ""))
    elif msg == "summary_needed":
        meta = _fmt_meta(
            model=rec.get("summary_model"),
            prompt_tok=rec.get("prompt_tok_est"),
            threshold=rec.get("threshold"),
            head=rec.get("head_n"),
            tail=rec.get("tail_n"),
            middle=rec.get("middle_count"),
        )
        add_section("SUMMARY_NEEDED", ANSI_MAGENTA, meta=meta)
    elif msg == "summary_req":
        meta = _fmt_meta(
            model=rec.get("summary_model"),
            middle=rec.get("middle_count"),
            transcript_chars=rec.get("transcript_chars"),
        )
        add_section("SUMMARY_REQ", ANSI_MAGENTA, meta=meta)
    elif msg == "summary_reply":
        meta = _fmt_meta(
            elapsed_ms=rec.get("elapsed_ms"),
            usage=_fmt_usage(rec.get("usage")),
        )
        add_section("SUMMARY_REPLY", ANSI_MAGENTA, _normalize_summary_text(rec.get("summary_snip", "")), meta=meta)
    elif msg == "summary_cache_lookup":
        meta = _fmt_meta(fingerprint=rec.get("fingerprint"), candidates=rec.get("candidates"))
        add_section("SUMMARY_CACHE_LOOKUP", ANSI_BLUE, meta=meta)
    elif msg == "summary_cache_hit":
        meta = _fmt_meta(fingerprint=rec.get("fingerprint"), range=rec.get("range"), appended_raw=rec.get("appended_raw"), final_last_idx=rec.get("final_last_idx"))
        add_section("SUMMARY_CACHE_HIT", ANSI_GREEN, meta=meta)
    elif msg == "summary_cache_miss":
        meta = _fmt_meta(fingerprint=rec.get("fingerprint"))
        add_section("SUMMARY_CACHE_MISS", ANSI_RED, meta=meta)
    elif msg == "summary_cache_save":
        meta = _fmt_meta(range=rec.get("range"), path=rec.get("path"))
        add_section("SUMMARY_CACHE_SAVE", ANSI_BLUE, meta=meta)
    elif msg == "summary_consolidate":
        meta = _fmt_meta(range=rec.get("range"))
        add_section("SUMMARY_CONSOLIDATE", ANSI_MAGENTA, meta=meta)
    elif msg == "upstream_req_repacked":
        kind = rec.get("kind", "chat")
        meta = _fmt_meta(
            kind=kind,
            model=rec.get("model"),
            prompt_tokens=rec.get("prompt_tokens"),
            did_summarize=rec.get("did_summarize"),
            archived=rec.get("has_archived_context"),
            max_tokens=rec.get("adjusted_max_tokens") or rec.get("max_tokens"),
        )
        add_section("CALL", ANSI_YELLOW, meta=meta)
        last_user = rec.get("last_user")
        if last_user:
            parts.append(_c("│   last_user:", ANSI_DIM, ANSI_GRAY))
            parts.append(_indent_block(last_user, prefix="│     "))
    elif msg == "http_out":
        meta = _fmt_meta(
            model=rec.get("model"),
            elapsed_ms=rec.get("elapsed_ms"),
            usage=_fmt_usage(rec.get("usage")),
        )
        add_section("RESULT", ANSI_GREEN, meta=meta)
        assistant = rec.get("assistant_text")
        if assistant:
            parts.append(_c("│   assistant:", ANSI_DIM, ANSI_GRAY))
            parts.append(_indent_block(assistant, prefix="│     "))
        tool_calls = rec.get("tool_calls")
        if tool_calls:
            parts.append(_c(f"│   tool_calls: {tool_calls}", ANSI_DIM, ANSI_GRAY))
    elif msg == "response_stream_reconstructed":
        meta = _fmt_meta(
            model=rec.get("upstream_model"),
            elapsed_ms=rec.get("elapsed_ms"),
            usage=_fmt_usage(rec.get("usage")),
        )
        add_section("STREAM_RESULT", ANSI_GREEN, meta=meta)
        assistant = rec.get("assistant_text")
        if assistant:
            parts.append(_c("│   assistant:", ANSI_DIM, ANSI_GRAY))
            parts.append(_indent_block(assistant, prefix="│     "))
    elif msg == "stream_progress":
        meta = _fmt_meta(
            model=rec.get("upstream_model"),
            elapsed_ms=rec.get("elapsed_ms"),
            ttft_ms=rec.get("ttft_ms"),
            generated_tokens_est=rec.get("generated_tokens_est"),
            tps_live=rec.get("tps_live"),
            events=rec.get("event_count"),
        )
        add_section("STREAM_PROGRESS", ANSI_BLUE, meta=meta)
    else:
        add_section(msg or "LOG", ANSI_BLUE, _snip_text_active(snip_json(rec), BASIC_SNIP_CHARS))

    footer = _maybe_close_plain_request(msg, req_id)
    if footer:
        parts.append(footer)
    return "\n".join(parts)


def _ensure_serializable(obj: Any) -> Any:
    """Convert non-serializable objects to serializable equivalents."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _ensure_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_ensure_serializable(item) for item in obj]
    # For other types, convert to string representation
    try:
        return str(obj)
    except Exception:
        return f"<unserializable {type(obj).__name__}>"


def log(level: str, msg: str, **fields: Any) -> None:
    if not _should_log(msg):
        return
    
    # Also write to keeprollming.log file (server log format)
    _log_to_file(level, msg, **fields)
    
    rec = {"ts": _ts(), "level": level.upper(), "msg": msg}
    # Ensure all fields are JSON serializable
    for k, v in fields.items():
        rec[k] = _ensure_serializable(v)

    if LOG_MODE == "BASIC_PLAIN":
        print(_format_plain(rec))
        return
    print_json(data=rec)


def _log_to_file(level: str, msg: str, **fields: Any) -> None:
    """Log to keeprollming.log file in server log format (one line per entry)."""
    try:
        # Build a concise message for the file logger with all fields
        extra_parts = []
        for k, v in fields.items():
            # Skip internal fields and None values
            if k in ("_truncated",) or v is None:
                continue
            # Convert to string representation
            if isinstance(v, (dict, list)):
                try:
                    extra_parts.append(f"{k}={json.dumps(v)}")
                except Exception:
                    extra_parts.append(f"{k}={type(v).__name__}")
            elif hasattr(v, '__dict__'):
                # Handle custom objects
                try:
                    extra_parts.append(f"{k}={v.__class__.__name__}")
                except Exception:
                    extra_parts.append(f"{k}=object")
            else:
                extra_parts.append(f"{k}={v}")

        extra_str = " | ".join(extra_parts) if extra_parts else ""
        full_msg = f"{msg}" + (f" | {extra_str}" if extra_str else "")

        # Map log levels
        level_map = {
            "DEBUG": "debug",
            "INFO": "info",
            "WARN": "warning",
            "ERROR": "error"
        }
        log_level = level_map.get(level, "info")

        server_logger = get_server_logger()
        if server_logger:
            getattr(server_logger, log_level, server_logger.info)(full_msg)
    except Exception:
        # Never let file logging failures break the app
        pass


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
    """Convert object to JSON string with fallback for non-serializable objects."""
    from keeprollming.routing import _UNSET
    
    def custom_serializer(o):
        """Custom JSON serializer that handles _UNSET and other special types."""
        if o is _UNSET:
            return "<UNSET>"  # Sentinel value - not serializable
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    
    try:
        s = json.dumps(obj, ensure_ascii=False, default=custom_serializer)
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


# ----------------------------
# Server File Logging Setup
# ----------------------------

_SERVER_LOGGER: logging.Logger | None = None
_LOG_FILE_PATH = os.getenv("LOG_FILE", "keeprollming.log")
_LOG_LEVEL = os.getenv("SERVER_LOG_LEVEL", "INFO").upper()


def setup_server_logging() -> logging.Logger:
    """
    Initialize server file logging.
    
    Creates a logger that writes to keeprollming.log with INFO level by default.
    Returns the configured logger instance.
    
    Call this function at application startup (e.g., in app.py main entry point).
    """
    global _SERVER_LOGGER
    
    if _SERVER_LOGGER is not None:
        return _SERVER_LOGGER  # Already initialized
    
    _SERVER_LOGGER = logging.getLogger("keeprollming.server")
    _SERVER_LOGGER.setLevel(getattr(logging, _LOG_LEVEL, "INFO"))
    
    # Avoid duplicate handlers
    if _SERVER_LOGGER.handlers:
        return _SERVER_LOGGER
    
    # File handler with rotation (10MB max, 3 backup files)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            _LOG_FILE_PATH,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setLevel(getattr(logging, _LOG_LEVEL, "INFO"))
    except Exception as e:
        print(f"Warning: Could not create log file {_LOG_FILE_PATH}: {e}")
        return _SERVER_LOGGER
    
    # Console handler (only for errors by default)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    
    # Formatter with timestamp, level, and message
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    _SERVER_LOGGER.addHandler(file_handler)
    _SERVER_LOGGER.addHandler(console_handler)
    
    # Log startup info
    _SERVER_LOGGER.info(f"Server logging initialized: {_LOG_FILE_PATH} (level={_LOG_LEVEL})")
    
    return _SERVER_LOGGER


def get_server_logger() -> logging.Logger | None:
    """Get the server logger instance, initializing it if needed."""
    global _SERVER_LOGGER
    if _SERVER_LOGGER is None:
        return setup_server_logging()
    return _SERVER_LOGGER


def log_server_event(level: str, message: str, **kwargs) -> None:
    """Log a server event to the file logger."""
    logger = get_server_logger()
    if logger:
        extra_msg = " | ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        full_message = f"{message} {extra_msg}".strip()
        getattr(logger, level.lower(), "info")(full_message)


def log_config_reload(old_mtime: float, new_mtime: float) -> None:
    """Log a config reload event."""
    log_server_event("INFO", "Config reloaded",
                     old_mtime=old_mtime,
                     new_mtime=new_mtime)


def log_config_error(error: str) -> None:
    """Log a config error during reload."""
    log_server_event("ERROR", f"Config reload failed: {error}")


def categorize_httpx_error(e: Exception) -> tuple[str, str]:
    """
    Categorize an httpx exception and return (error_type, error_message).
    
    Returns:
        Tuple of (error_type string, truncated error message)
    """
    import httpx
    
    err_type = "unknown"
    err_msg = str(e)[:500]
    
    if isinstance(e, httpx.ConnectError):
        err_type = "connection_failed"
        # Try to extract target URL from error
        err_msg = _extract_connection_target(str(e)) or f"Connection failed: {str(e)[:200]}"
    elif isinstance(e, httpx.ConnectTimeout):
        err_type = "connection_timeout"
        err_msg = _extract_connection_target(str(e)) or f"Connection timeout"
    elif isinstance(e, httpx.TimeoutException):
        err_type = "timeout"
        err_msg = str(e)[:200]
    elif isinstance(e, httpx.NetworkError):
        err_type = "network_error"
        err_msg = str(e)[:200]
    elif isinstance(e, httpx.HTTPStatusError):
        err_type = "http_status_error"
        status = getattr(e, 'response', None)
        if status:
            err_msg = f"HTTP {status.status_code}: {str(e)[:150]}"
    
    return err_type, err_msg


def _extract_connection_target(error_str: str) -> str | None:
    """Extract target URL/host from connection error string."""
    import re
    
    # Try to find a URL pattern
    url_match = re.search(r'([a-zA-Z]+://[^\s\)]+)', error_str)
    if url_match:
        return url_match.group(1)[:200]
    
    # Try to find a host:port pattern
    host_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', error_str)
    if host_match:
        return host_match.group(1)
    
    return None


def log_request_error(req_id: str, error_type: str, endpoint: str | None = None,
                      model: str | None = None, upstream_url: str | None = None,
                      status_code: int | None = None, elapsed_ms: float | None = None,
                      **extra_fields) -> None:
    """
    Centralized error logging function for request errors.
    
    Logs errors with consistent categorization and context.
    """
    log(
        "ERROR",
        "request_error",
        req_id=req_id,
        error_type=error_type,
        endpoint=endpoint,
        model=model,
        upstream_url=upstream_url,
        status=status_code,
        elapsed_ms=elapsed_ms,
        **extra_fields
    )


def log_connection_error(req_id: str, error_type: str, upstream_url: str,
                         model: str | None = None, elapsed_ms: float | None = None,
                         **extra_fields) -> None:
    """
    Centralized connection error logging function.
    
    Specifically for httpx.ConnectError, ConnectTimeout, and similar errors.
    """
    log(
        "ERROR",
        "connection_error",
        req_id=req_id,
        error_type=error_type,
        upstream_url=upstream_url,
        model=model,
        elapsed_ms=elapsed_ms,
        **extra_fields
    )


def log_fallback_error(req_id: str, from_model: str, to_model: str,
                       error_type: str, err_msg: str, **extra_fields) -> None:
    """Centralized fallback chain error logging."""
    log(
        "WARN",
        "fallback_error",
        req_id=req_id,
        from_model=from_model,
        to_model=to_model,
        error_type=error_type,
        err_msg=err_msg[:500],
        **extra_fields
    )
