"""Plain-text (BASIC_PLAIN mode) formatting logic."""

from __future__ import annotations

import json
import textwrap
from datetime import datetime
from typing import Any, Dict


def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI color codes."""
    from .constants import LOG_PLAIN_COLORS, ANSI_RESET
    if not LOG_PLAIN_COLORS or not codes:
        return text
    return "".join(codes) + text + ANSI_RESET


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from a string."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# Import shared helpers from formatters.py (used by add_section_plain and format_plain)
from .formatters import _indent_block, _fmt_meta_item as _fm_i  # noqa: F401


def add_section_plain(parts: list[str], title: str, body: str | None = None, meta: str | None = None) -> None:
    """Add a formatted section line + optional indented block to parts."""
    from .constants import ANSI_BOLD, LOG_PLAIN_WRAP_WIDTH as wrap_width

    # Title with pipe prefix and colors
    title_txt = _c(f"│ {title}", ANSI_BOLD)
    if meta:
        title_txt += " " + meta

    plain_title = _strip_ansi(title_txt)
    available = max(0, wrap_width - len(_strip_ansi("│   ")))

    # Wrap long titles
    if wrap_width > 0 and len(plain_title) > wrap_width:
        wrapped = textwrap.wrap(
            title_txt, width=wrap_width, replace_whitespace=False,
            drop_whitespace=False, break_long_words=False, break_on_hyphens=False,
            subsequent_indent="│   ",
        )
        parts.extend(wrapped or [title_txt])
    else:
        parts.append(title_txt)

    # Indent body if present — _indent_block returns list[str] from formatters.py
    if body is not None:
        for line in _indent_block(body):  # imported at module level
            parts.append(line)


def format_stream_progress(fields: list[str]) -> list[str]:
    """Format a stream_progress log entry with right-aligned numeric padding."""
    from .constants import ANSI_BOLD, ANSI_BLUE

    title_txt = _c("│ STREAM_PROGRESS", ANSI_BOLD, ANSI_BLUE)
    
    # Apply padding to each field for better alignment when values change width
    padded_fields = []
    for field in fields:
        if "=" not in field:
            padded_fields.append(field)
            continue
        
        key, value = field.split("=", 1)
        # Right-align numeric values with appropriate padding
        if key == "events":
            padded_value = str(value).rjust(6)
        elif key == "elapsed_ms":
            padded_value = str(value).rjust(6)
        elif key == "ttft_ms":
            padded_value = str(value).rjust(6)
        elif key == "generated_tokens_est":
            padded_value = str(value).rjust(6)
        elif key == "content_chars":
            padded_value = str(value).rjust(6)
        elif key == "tps_live":
            # For floats, ensure consistent width (e.g., " 123.4")
            padded_value = str(value).rjust(7)
        else:
            padded_value = value
        
        padded_fields.append(f"{key}={padded_value}")
    
    meta_str = "    ".join(padded_fields) if padded_fields else ""
    return [title_txt + ("    " + meta_str if meta_str else "")]


def format_plain(rec: Dict[str, Any]) -> str:
    """Format a log record dict as plain-text output for BASIC_PLAIN mode.

    This is the core formatter used when LOG_MODE == "BASIC_PLAIN".
    It dispatches on rec["msg"] and produces ANSI-colored pipe-aligned text.
    """
    from ..logger import (
        BASIC_SNIP_CHARS, _should_log, _normalize_summary_text,
        _fmt_usage, _indent_block as lb_indent_block, _strip_ansi as strip_ansi_local,
    )

    # Import formatters helpers used in dispatch below
    from .formatters import _fmt_meta, _fmt_tool_calls_yaml, _fmt_tool_result_yaml

    msg = rec.get("msg")
    req_id = rec.get("req_id", "-")
    parts: list[str] = []

    # ── Open request block if needed ────────────────────────────────
    header = _open_plain_request_if_needed(req_id)
    if header:
        parts.append(header)

    from .constants import ANSI_CYAN, ANSI_BLUE, ANSI_YELLOW, ANSI_GREEN, ANSI_MAGENTA, ANSI_RED, ANSI_DIM, ANSI_GRAY

    # ── Message-type dispatch ────────────────────────────────────────
    def add_meta(**kw):
        return _fmt_meta(**kw) if any(v is not None for v in kw.values()) else ""

    if msg == "conv_user":
        add_section_plain(parts, "USER", rec.get("text", ""))

    elif msg == "http_in":
        meta = _fmt_meta(
            model=rec.get("client_model"), stream="yes" if rec.get("stream") else "no",
            tokens=rec.get("message_count"), max_tokens=rec.get("max_tokens"),
        )
        add_section_plain(parts, "HTTP_IN", None, meta)

    elif msg == "route_resolved":
        # Build route hierarchy string: code/architect > code > base/deep > arkai/lmstudio
        resolved_route = rec.get("resolved_route", "")
        parent_routes = rec.get("parent_routes", [])
        
        if parent_routes and isinstance(parent_routes, list):
            # Reverse to show from most specific to base
            hierarchy = " > ".join(reversed(parent_routes + [resolved_route]))
        else:
            hierarchy = resolved_route
        
        meta = _fmt_meta(
            route=hierarchy, model=rec.get("model"),
            upstream_model=rec.get("upstream_model"), ctx_len=rec.get("ctx_len"),
            max_tokens=rec.get("max_tokens_default"),
        )
        add_section_plain(parts, "ROUTE", None, meta)

    elif msg == "conv_system":
        add_section_plain(parts, "SYSTEM", rec.get("text", ""))

    elif msg == "conv_assistant":
        add_section_plain(parts, "ASSISTANT", rec.get("text", ""))

    elif msg == "summary_needed":
        meta = _fmt_meta(
            model=rec.get("summary_model"), prompt_tok=rec.get("prompt_tok_est"),
            threshold=rec.get("threshold"), head=rec.get("head_n"), tail=rec.get("tail_n"),
            middle=rec.get("middle_count"),
        )
        add_section_plain(parts, "SUMMARY_NEEDED", None, meta)

    elif msg == "summary_req":
        meta = _fmt_meta(
            model=rec.get("summary_model"), middle=rec.get("middle_count"),
            transcript_chars=rec.get("transcript_chars"),
        )
        add_section_plain(parts, "SUMMARY_REQ", None, meta)

    elif msg == "summary_reply":
        meta = _fmt_meta(
            elapsed_ms=rec.get("elapsed_ms"), usage=_fmt_usage(rec.get("usage")),
        )
        body = _normalize_summary_text(rec.get("summary_snip", "")) if rec.get("summary_snip") else None
        add_section_plain(parts, "SUMMARY_REPLY", body, meta)

    elif msg == "summary_cache_lookup":
        meta = _fmt_meta(fingerprint=rec.get("fingerprint"), candidates=rec.get("candidates"))
        add_section_plain(parts, "SUMMARY_CACHE_LOOKUP", None, meta)

    elif msg == "summary_cache_hit":
        meta = _fmt_meta(
            fingerprint=rec.get("fingerprint"), range=rec.get("range"),
            appended_raw=rec.get("appended_raw"), final_last_idx=rec.get("final_last_idx"),
        )
        add_section_plain(parts, "SUMMARY_CACHE_HIT", None, meta)

    elif msg == "summary_cache_miss":
        meta = _fmt_meta(fingerprint=rec.get("fingerprint"))
        add_section_plain(parts, "SUMMARY_CACHE_MISS", None, meta)

    elif msg == "summary_cache_save":
        meta = _fmt_meta(range=rec.get("range"), path=rec.get("path"))
        add_section_plain(parts, "SUMMARY_CACHE_SAVE", None, meta)

    elif msg == "summary_bypassed":
        # Format as SUMMARY_BYPASSED reason=X prompt_tok_est=Y threshold=Z (key=value format)
        reason = rec.get("reason", "unknown")
        prompt_tok_est = rec.get("prompt_tok_est", 0)
        threshold = rec.get("threshold", 0)
        meta_str = f"reason={reason} prompt_tok_est={prompt_tok_est} threshold={threshold}"
        parts.append(f"│ SUMMARY_BYPASSED {meta_str}")

    elif msg == "summary_consolidate":
        meta = _fmt_meta(range=rec.get("range"))
        add_section_plain(parts, "SUMMARY_CONSOLIDATE", None, meta)

    elif msg == "upstream_req_repacked":
        kind = rec.get("kind", "chat")
        meta = _fmt_meta(
            kind=kind, model=rec.get("model"), prompt_tokens=rec.get("prompt_tokens"),
            did_summarize=rec.get("did_summarize"), archived=rec.get("has_archived_context"),
            max_tokens=rec.get("adjusted_max_tokens") or rec.get("max_tokens"),
        )
        add_section_plain(parts, "CALL", None, meta)
        last_user = rec.get("last_user")
        if last_user:
            parts.append(f"│   last_user")
            for line in _indent_block(last_user, prefix="│     "):  # uses module-level import from formatters.py
                parts.append(line)

    elif msg == "request_sent":
        meta = _fmt_meta(
            method=rec.get("method"), url=str(rec.get("url", ""))[:100],
        )
        add_section_plain(parts, "HTTP_OUTBOUND", None, meta)

        tool_calls = rec.get("tool_calls")
        if tool_calls:
            tc_yaml = _fmt_tool_calls_yaml(tool_calls)
            if tc_yaml:
                parts.append(f"│   TOOL_CALLS")
                for line in tc_yaml.split("\n"):
                    parts.append("│       " + line)

        tool_results = rec.get("tool_results")
        if tool_results:
            for tr in tool_results:
                formatted = _fmt_tool_result_yaml(
                    tr.get("tool_call_id"), tr.get("name"), tr.get("result") or tr.get("content"))
                if formatted:
                    parts.append(f"│   TOOL_RESULT")
                    for line in formatted.split("\n"):
                        if line.strip():
                            parts.append("│       " + line)

    elif msg == "http_out":
        meta = _fmt_meta(
            model=rec.get("model"), elapsed_ms=rec.get("elapsed_ms"),
            usage=_fmt_usage(rec.get("usage")),
        )
        add_section_plain(parts, "RESULT", None, meta)

        assistant = rec.get("assistant_text")
        if assistant:
            parts.append(f"│   assistant")
            for line in assistant.split("\n"):
                parts.append("│     " + line)

        tool_calls = rec.get("tool_calls")
        if tool_calls:
            tc_yaml = _fmt_tool_calls_yaml(tool_calls)
            if tc_yaml:
                parts.append(f"│   TOOL_CALLS")
                for line in tc_yaml.split("\n"):
                    parts.append("│       " + line)

    elif msg == "response_stream_reconstructed":
        meta = _fmt_meta(
            model=rec.get("upstream_model"), elapsed_ms=rec.get("elapsed_ms"),
            ttft_ms=rec.get("ttft_ms"), tps=rec.get("tps"), usage=_fmt_usage(rec.get("usage")),
        )
        add_section_plain(parts, "STREAM_RESULT", None, meta)

        assistant = rec.get("assistant_text")
        if assistant:
            parts.append(f"│   ASSISTANT")
            for line in assistant.split("\n"):
                parts.append("│     " + line)

        tool_calls = rec.get("tool_calls")
        if tool_calls:
            tc_yaml = _fmt_tool_calls_yaml(tool_calls)
            if tc_yaml:
                parts.append(f"│   TOOL_CALLS")
                for line in tc_yaml.split("\n"):
                    if line.strip():
                        parts.append("│     " + line)

        if BASIC_SNIP_CHARS == 0:
            response_body = rec.get("response_body")
            if response_body:
                parts.append(f"│   RESPONSE_BODY:")
                try:
                    raw_json = json.dumps(response_body, indent=2, ensure_ascii=False)
                    for line in _indent_block(raw_json, prefix="│     "):  # uses module-level import from formatters.py
                        parts.append(line)
                except Exception:
                    pass

    elif msg == "stream_progress":
        fields = []
        events = rec.get("event_count")
        if events is not None:
            fields.append(f"events={events}")
        elapsed_ms = rec.get("elapsed_ms")
        if elapsed_ms is not None:
            fields.append(f"elapsed_ms={int(elapsed_ms)}")
        ttft_ms = rec.get("ttft_ms")
        if ttft_ms is not None:
            fields.append(f"ttft_ms={ttft_ms}")
        gen_est = rec.get("generated_tokens_est")
        if gen_est is not None:
            fields.append(f"generated_tokens_est={gen_est}")
        cc = rec.get("content_chars")
        if cc is not None:
            fields.append(f"content_chars={cc}")
        tps_live = rec.get("tps_live")
        if tps_live is not None:
            fields.append(f"tps_live={round(tps_live, 1)}")

        parts.extend(format_stream_progress(fields))

    elif msg == "tool_call":
        tc_list = rec.get("tool_calls") or rec.get("function_calls")
        if tc_list:
            tc_yaml = _fmt_tool_calls_yaml(tc_list)
            if tc_yaml:
                parts.append(f"│ TOOL_CALLS")
                for line in tc_yaml.split("\n"):
                    if line.strip():
                        parts.append("│   " + line)

    elif msg == "tool_result":
        tool_call_id = rec.get("tool_call_id") or rec.get("id")
        name = rec.get("name")
        result = rec.get("result") or rec.get("content")

        formatted = _fmt_tool_result_yaml(tool_call_id, name, result)
        if formatted:
            title_txt = f"│ TOOL_RESULT [{tool_call_id}]" if tool_call_id else "│ TOOL_RESULT"
            parts.append(title_txt)
            for line in formatted.split("\n"):
                parts.append("│   " + line)

    elif msg == "override_applied":
        param = rec.get("param", "?")
        old_val = rec.get("old_value", "<none>")
        new_val = rec.get("new_value", "<none>")
        meta = _fmt_meta(param=param, old=str(old_val), new=str(new_val))
        add_section_plain(parts, "OVERRIDE_APPLIED", None, meta)

    elif msg == "filter_triggered_nudge":
        filter_action = rec.get("filter_action", "nudge")
        message = rec.get("message", "")[:50] if rec.get("message") else ""
        meta = _fmt_meta(filter="model_nudge", action=filter_action, message=message)
        add_section_plain(parts, "FILTER_TRIGGERED", None, meta)

    elif msg == "nudge_retry_attempt":
        attempt = rec.get("attempt", 1)
        nudge_message = rec.get("nudge_message", "")[:30] if rec.get("nudge_message") else ""
        total_messages = rec.get("total_messages", 0)
        meta = _fmt_meta(attempt=attempt, message=nudge_message, messages=total_messages)
        add_section_plain(parts, "NUDGE_RETRY", None, meta)

    elif msg == "nudge_retry_success":
        add_section_plain(parts, "NUDGE_RETRY_SUCCESS", "Retry completed successfully")

    elif msg == "nudge_retry_failed" or msg == "nudge_retry_error":
        error_type = "FAILED" if msg == "nudge_retry_failed" else "ERROR"
        add_section_plain(parts, f"NUDGE_RETRY_{error_type}", rec.get("reason", str(rec.get("error", "")))[:50])

    elif msg == "assistant":
        content = rec.get("content", "") or ""
        total_length = rec.get("total_length", 0)
        reasoning = rec.get("reasoning_content", "") or ""
        reasoning_length = rec.get("reasoning_length", 0)

        # Reasoning block (before assistant)
        if reasoning:
            reasoning_meta = _fmt_meta(length=reasoning_length) if reasoning_length else ""
            add_section_plain(parts, "REASONING", reasoning, reasoning_meta or None)

        # Assistant block
        meta = _fmt_meta(length=total_length) if total_length else ""
        tcs = rec.get("tool_calls")
        if tcs:
            meta += f" | tool_calls=[{', '.join(tcs[:3])}]"
        add_section_plain(parts, "ASSISTANT", content or None, meta or None)

    elif msg == "filter_chain_executed":
        filters = rec.get("filters", [])
        nudge_attempts = rec.get("nudge_attempts", 0)
        meta = _fmt_meta(filters=",".join(filters), nudge_attempts=nudge_attempts)
        add_section_plain(parts, "FILTER_CHAIN_EXECUTED", None, meta)

    elif msg in ("upstream_http_error", "upstream_http_error_stream", "upstream_stream_exception",
                 "upstream_request_failed", "streaming_error"):
        status = rec.get("status", "")
        body = rec.get("body", "") or rec.get("error", "")
        route = rec.get("route", "")
        url = rec.get("url", "")
        upstream_model = rec.get("upstream_model", "")
        meta = _fmt_meta(status=status, route=route, model=upstream_model, url=url)
        if body:
            # Show first 200 chars of error body
            body_short = body[:200] if len(body) > 200 else body
            add_section_plain(parts, "UPSTREAM_ERROR", body_short, meta)
        else:
            add_section_plain(parts, "UPSTREAM_ERROR", None, meta)

    elif msg == "nudge_retry_start":
        meta = _fmt_meta(
            pattern=rec.get("pattern"), max_attempts=rec.get("max_attempts"),
            content_length=rec.get("content_length"),
        )
        add_section_plain(parts, "NUDGE_RETRY_START", None, meta)

    elif msg == "assistant_nudged_response":
        content = rec.get("content", "")[:5000] if rec.get("content") else ""
        content = content.replace("\n", " | ")
        attempts = rec.get("attempts", 0)
        meta = _fmt_meta(content=content, attempts=attempts)
        add_section_plain(parts, "ASSISTANT_NUDGED_RESPONSE", None, meta)

    elif msg == "USER_NUDGE":
        message = rec.get("message", "")
        attempt = rec.get("attempt", 0)
        max_attempts = rec.get("max_attempts", 0)
        meta = _fmt_meta(attempt=f"{attempt}/{max_attempts}")
        add_section_plain(parts, "USER_NUDGE", f"  {message}", meta)



    elif msg == "nudge_retry_request":
        model = rec.get("model", "")
        messages_count = rec.get("messages_count", 0)
        meta = _fmt_meta(model=model, messages_count=messages_count)
        add_section_plain(parts, "NUDGE_RETRY", None, meta)

    elif msg == "nudge_retry_raw_response":
        content = rec.get("content", "")[:5000] if rec.get("content") else ""
        content = content.replace("\n", " | ")
        total_length = rec.get("total_length", 0)
        attempt = rec.get("attempt", 0)
        meta = _fmt_meta(content=content, length=total_length, attempt=attempt)
        add_section_plain(parts, "NUDGE_RESPONSE", None, meta)

    elif msg == "assistant_lazy_response":
        content = rec.get("content", "")[:5000] if rec.get("content") else ""
        content = content.replace("\n", " | ")
        total_length = rec.get("total_length", 0)
        meta = _fmt_meta(content=content, length=total_length)
        add_section_plain(parts, "ASSISTANT_LAZY_RESPONSE", None, meta)

    elif msg == "assistant_after_nudge":
        content = rec.get("content", "")[:5000] if rec.get("content") else ""
        total_length = rec.get("total_length", 0)
        nudge_attempts = rec.get("nudge_attempts", 0)
        meta = _fmt_meta(content=content, length=total_length, attempts=nudge_attempts)
        add_section_plain(parts, "ASSISTANT_AFTER_NUDGE", None, meta)

    elif msg == "assistant_final_response":
        content = rec.get("content", "")[:5000] if rec.get("content") else ""
        content = content.replace("\n", " | ")  # sanitize newlines
        total_length = rec.get("total_length", 0)
        nudge_attempts = rec.get("nudge_attempts", 0)
        meta = _fmt_meta(content=content, length=total_length, attempts=nudge_attempts)
        add_section_plain(parts, "ASSISTANT_FINAL_RESPONSE", None, meta)

    # ── Cache metrics formatter ──────────────────────────────────────

    elif msg == "cache_metrics":
        cached_tokens = rec.get("cached_tokens")
        prompt_tokens = rec.get("prompt_tokens")
        cache_pct = rec.get("cache_pct")
        meta = _fmt_meta(cached=cached_tokens, prompt=prompt_tokens, pct=f"{cache_pct}%")
        add_section_plain(parts, "CACHE", None, meta)

    # ── TLS Tool Loop Stopper formatters ──────────────────────────────

    elif msg == "tool_loop_detected":
        function_name = rec.get("function_name", "?")
        repeated = rec.get("repeated", False)
        meta = _fmt_meta(function_name=function_name, repeated=repeated)
        add_section_plain(parts, "TLS_DETECTED", None, meta)

    elif msg == "tls_intervention":
        injected = rec.get("injected_tool_result", False)
        messages_count = rec.get("messages_count", 0)
        meta = _fmt_meta(injected_tool_result=injected, messages_count=messages_count)
        add_section_plain(parts, "TLS_INTERVENTION", None, meta)

    elif msg == "tls_retry":
        model = rec.get("model", "?")
        messages_count = rec.get("messages_count", 0)
        meta = _fmt_meta(model=model, messages_count=messages_count)
        add_section_plain(parts, "TLS_RETRY", None, meta)

    elif msg == "tls_response":
        content = rec.get("content", "")[:5000] if rec.get("content") else ""
        content = content.replace("\n", " | ")
        total_length = rec.get("length", 0)
        meta = _fmt_meta(content=content, length=total_length)
        add_section_plain(parts, "TLS_RESPONSE", None, meta)

    elif msg == "tls_fallback":
        reason = rec.get("reason", "?")
        meta = _fmt_meta(reason=reason)
        add_section_plain(parts, "TLS_FALLBACK", None, meta)

    # ── System Prompt formatters ──────────────────────────────────

    elif msg == "system_prompt_inserted":
        prompt_preview = rec.get("prompt_preview", "")[:80]
        meta = _fmt_meta(prompt_preview=prompt_preview)
        add_section_plain(parts, "SYSTEM_PROMPT", "INSERTED", meta)

    elif msg == "system_prompt_overridden":
        prompt_preview = rec.get("prompt_preview", "")[:80]
        old_length = rec.get("old_length", 0)
        meta = _fmt_meta(prompt_preview=prompt_preview, old_length=old_length)
        add_section_plain(parts, "SYSTEM_PROMPT", "OVERRIDDEN", meta)

    elif msg == "system_prompt_prepended":
        prompt_preview = rec.get("prompt_preview", "")[:80]
        old_length = rec.get("old_length", 0)
        meta = _fmt_meta(prompt_preview=prompt_preview, old_length=old_length)
        add_section_plain(parts, "SYSTEM_PROMPT", "PREPENDED", meta)

    elif msg == "tls_missing_upstream_url":
        add_section_plain(parts, "TLS_ERROR", "  MISSING UPSTREAM URL", None)

    else:
        # Generic fallback — log the raw record as JSON
        try:
            body = json.dumps(rec, ensure_ascii=False, default=str)
        except Exception:
            body = str(rec)
        snipped = _snip_text_active(body, BASIC_SNIP_CHARS) if BASIC_SNIP_CHARS > 0 else body
        add_section_plain(parts, msg or "LOG", snipped)

    # ── Close request block if terminal message ────────────────────
    footer = _close_plain_request_if_needed(msg, req_id)
    if footer:
        parts.append(footer)

    return "\n".join(parts)


# ── Plain-text state helpers (module-level globals) ────────────────

_plain_last_req_id: str | None = None


def _open_plain_request_if_needed(req_id: str) -> str | None:
    """Open a new plain-text request block if needed."""
    global _plain_last_req_id  # type: ignore[name-defined]

    from ..logger import _PLAIN_CLOSED_REQ_IDS as closed_ids, _PLAIN_LAST_REQ_ID as last_from_logger

    # Skip if req_id is empty or placeholder "-"
    if not req_id or req_id == "-":
        return None

    prev = _plain_last_req_id

    # If same req_id is already open, don't reopen - just return None
    if prev == req_id:
        return None

    dt = datetime.now().strftime("%Y%m%d-%H%M%S")

    # If switching to a different req_id, mark the old one as needing closure
    # (actual close happens via _close_plain_request_if_needed on terminal messages)
    if prev and prev != req_id and prev not in closed_ids:
        closed_ids.add(prev)

    _plain_last_req_id = req_id
    return f"┌─ REQUEST {req_id} [{dt}]"


def _close_plain_request_if_needed(msg: str, req_id: str) -> str | None:
    """Close a plain-text request block if this is a terminal message."""
    global _plain_last_req_id  # type: ignore[name-defined]

    from .constants import ANSI_DIM, ANSI_GRAY

    # Skip if req_id is empty or placeholder "-"
    if not req_id or req_id == "-":
        return None

    terminal_msgs = {
        "http_out",
        "response_stream_reconstructed",
        "proxy_exception",
        "upstream_http_error",
        "upstream_http_error_stream",
        "upstream_stream_exception",
        "upstream_request_failed",
        "streaming_error",
        "filter_chain_executed",
        "assistant",
    }
    if msg not in terminal_msgs:
        return None

    dt = datetime.now().strftime("%Y%m%d-%H%M%S")
    from ..logger import _PLAIN_CLOSED_REQ_IDS as closed_ids
    closed_ids.add(req_id)
    _plain_last_req_id = None  # type: ignore[name-defined]
    return f"└─ END {req_id} [{dt}]"


# ── Re-exported helpers needed by format_plain ─────────────────────

def _snip_text_active(s: str | None, limit: int) -> str:
    """Snip a string to the given character limit."""
    if s is None:
        return ""
    if limit <= 0:
        return s
    return s if len(s) <= limit else (s[:limit] + f"... <snip {len(s)-limit} chars>")


def _fmt_meta_item(key: str, value: Any) -> str:
    """Format a single metadata key=value pair."""
    from .constants import ANSI_DIM, ANSI_YELLOW
    return f"{key}={value}"  # simplified for plain text mode
