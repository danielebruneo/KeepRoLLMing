"""Public API for keeprollming logging — thin wrapper over modularized package."""

from __future__ import annotations

import json
import time
from typing import Any, Dict

# ── Re-export public symbols from submodules (backward compat) ─────
from .logging.constants import LOG_MODE, LOG_MODE_CHOICES, BASIC_SNIP_CHARS, LOG_SNIP_CHARS, LOG_PLAIN_COLORS, LOG_PLAIN_WRAP_WIDTH  # noqa: F401
from .logging.snipping import snip_json, _is_json_content_type  # noqa: F401
from .logging.message_utils import extract_last_user_text, get_text_content, classify_messages, _snip_obj_active  # noqa: F401
from .logging.payload_summary import summarize_request_payload, summarize_response_payload  # noqa: F401
from .logging.formatters import _fmt_meta_item, _fmt_meta, _fmt_tokens, _normalize_summary_text, _fmt_usage, _indent_block, _strip_ansi  # noqa: F401
from .logging.server_events import (  # noqa: F401
    setup_server_logging, get_server_logger, setup_debug_logging, get_debug_logger,
    log_server_event, log_exception, log_body, categorize_httpx_error, _extract_connection_target,
    log_request_error, log_connection_error, log_fallback_error,
    log_config_reload, log_config_error,
)

# ── Plain text formatter (aliased for backward compat) ─────────────
from .logging.plain_text import format_plain as _format_plain  # noqa: F401

# ── Internal helpers (used by log()) ───────────────────────────────


def _ts() -> float:
    return time.time()


_PLAIN_LAST_REQ_ID = None  # Sync with plain_text module state
_PLAIN_CLOSED_REQ_IDS: set[str] = set()

# ── NDJSON log file (always written) ───────────────────────────────

_JSON_LOG_PATH: str | None = None
_JSON_LOG_FH = None


def _get_json_log_path() -> str:
    """Resolve the JSON log file path from environment or default."""
    global _JSON_LOG_PATH
    if _JSON_LOG_PATH is None:
        import os
        log_dir = os.environ.get("LOG_PATH", ".")
        _JSON_LOG_PATH = os.path.join(log_dir, "keeprollming.log.json")
    return _JSON_LOG_PATH


def _write_json_log(rec: dict) -> None:
    """Append a JSON log entry to the NDJSON file (always written).

    Uses the AsyncLogWriter to avoid blocking the event loop on flush().
    Falls back to synchronous write if the writer is not available (e.g.,
    early startup or tests).
    """
    try:
        from .async_log_writer import get_async_writer
        writer = get_async_writer()
        if writer._running:
            writer.enqueue("json_log", rec)
            return
    except Exception:
        pass  # fall through to sync write below

    # Synchronous fallback — only used when the async writer isn't running
    global _JSON_LOG_FH
    try:
        if _JSON_LOG_FH is None:
            import os
            path = _get_json_log_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            _JSON_LOG_FH = open(path, "a", encoding="utf-8")
        _JSON_LOG_FH.write(json.dumps(rec, default=str) + "\n")
        _JSON_LOG_FH.flush()
    except Exception:
        pass  # Never let logging break the request


def _should_log(msg: str) -> bool:
    """Return True if this message type should be logged in the current LOG_MODE."""
    import os
    from .logging.constants import LOG_MODE as lm

    if lm == "DEBUG":
        return True
    if lm == "MEDIUM":
        return msg not in {"payload_in_full", "response_received"}
    # BASIC / BASIC_PLAIN: whitelist only, unless BASIC_PLAIN_VERBOSE is set
    if os.environ.get("BASIC_PLAIN_VERBOSE", "").lower() in ("1", "true", "yes"):
        return True
    return msg in {
        "startup", "http_in", "summary_needed", "summary_req", "summary_reply",
        "summary_cache_lookup", "summary_cache_hit", "summary_cache_miss",
        "summary_cache_save", "summary_consolidate", "repacked",
        "summary_failed_fallback_passthrough", "summary_bypassed",
        "max_tokens_clamped", "proxy_exception",
        "upstream_http_error", "upstream_http_error_stream", "upstream_stream_exception",
        "upstream_request_failed", "streaming_error",
        "pipeline_process_request_error", "pipeline_process_response_error",
        "conv_user", "conv_system", "conv_assistant",
        "response_stream_reconstructed", "stream_progress",
        "tool_call", "tool_result", "function_call",
        "upstream_req_repacked", "http_out", "override_applied", "route_resolved",
        "filter_triggered_nudge", "nudge_retry_attempt", "nudge_retry_success",
        "nudge_retry_failed", "nudge_retry_error", "filter_chain_executed",
        # ── Diagnostic / streaming pipeline events ─────────────────
        "chat_request_start", "chat_request_route",
        "stream_handler_entry", "stream_handler_pipeline",
        "pipeline_run_stream_start", "pipeline_run_stream_entry",
        "pipeline_phase1_done", "pipeline_phase2_start", "pipeline_phase2_end",
        "pipeline_stream_first_chunk", "pipeline_stream_retry",
        "pipeline_stream_stop", "pipeline_phase3_retry_start",
        "pipeline_phase4_fr_stop", "pipeline_no_upstream",
        "pipeline_phase4_error",
        "pipeline_run_stream_done", "pipeline_build",
        # ── Nudge streaming events ─────────────────────────────────
        "nudge_streaming_lazy_detected", "nudge_streaming_skip_buffer_has_tc",
        "nudge_response_empty", "nudge_response_valid",
        "nudge_skip_has_tool_calls", "nudge_skip_rewritten_tc",
        # ── Connection diagnostics ──────────────────────────────────
        "upstream_stream_closed", "upstream_stream_close",
        "downstream_closed", "downstream_complete",
        # ── Timestamp / nudge interaction ───────────────────────────
        "timestamp_replacing_stale", "timestamp_stripped_stale",
        "embedding_request", "embedding_request_failed",
        # Nudge retry observability logging
        "assistant_lazy_response", "assistant_after_nudge", "assistant_final_response",
        "USER_NUDGE", "nudge_no_upstream_url_configured", "nudge_retry_failed_giving_up",
        "nudge_retry_request", "nudge_no_response_choices", "nudge_retry_http_error",
        # Nudge retry start and response events
        "nudge_retry_start", "assistant_nudged_response",
        "nudge_skipped_has_tool_calls",
        # TLS Tool Loop Stopper events
        "tool_loop_detected", "tls_intervention", "tls_retry", "tls_response", "tls_fallback",
        "tls_filtered_repeated",
        "tls_missing_upstream_url",
        "tls_added_user_message",
        "tls_retry_timeout", "tls_retry_cancelled",
        "filter_process_request_error_streaming", "filter_modified_detected", "filter_not_modified", "finish_reason_override", "streaming_filter_debug",
        "filter_chain_check", "filter_chain_loaded", "filter_chain_execution_error",
        # System prompt events
        "system_prompt_inserted", "system_prompt_overridden", "system_prompt_prepended",
        # Timestamp filter events
        "timestamp_injected", "timestamp_appended",
        "timestamp_debug",
        # Conversation tracing for nudge retry
        # Nudge conversation tracing
        "nudge_retry_conversation_msg", "nudge_retry_raw_response",
        # Assistant response logging
        "assistant",
        # Cache metrics
        "cache_metrics",
    }


def _ensure_serializable(obj: Any) -> Any:
    """Convert non-serializable objects to JSON-safe equivalents."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return ''.join(c for c in obj if ord(c) >= 32 or c in '\n\r\t')
    if isinstance(obj, (int, float, bool)):
        return obj
    if isinstance(obj, dict):
        try:
            result = {}
            for k, v in obj.items():
                safe_key = str(k) if not isinstance(k, str) else k
                result[safe_key] = _ensure_serializable(v)
            return result
        except Exception:
            return f"<unserializable dict with type {type(obj).__name__}>"
    if isinstance(obj, (list, tuple)):
        try:
            return [_ensure_serializable(item) for item in obj]
        except Exception:
            return [f"<unserializable item of type {type(item).__name__}" for item in obj]

    try:
        if hasattr(obj, '__dict__'):
            return _ensure_serializable(obj.__dict__)
        if hasattr(obj, '_asdict') and callable(getattr(obj, '_asdict')):
            return _ensure_serializable(obj._asdict())
        s = str(obj)
        return ''.join(c for c in s if ord(c) >= 32 or c in '\n\r\t')
    except Exception:
        try:
            return f"<unserializable {type(obj).__name__}>"
        except Exception:
            return "<completely unserializable object>"


def _log_to_file(level: str, msg: str, **fields: Any) -> None:
    """Log to keeprollming.log file in server log format (one line per entry)."""
    try:
        SAFE_FIELDS = {
            "req_id", "level", "msg", "model", "client_model", "endpoint",
            "upstream_url", "status", "error_type", "elapsed_ms",
            "stream", "message_count", "max_tokens", "user_id", "conv_id",
            "reason", "prompt_tok_est", "threshold", "head_n", "tail_n",
            "middle_count", "kind", "did_summarize", "passthrough",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "ttft_ms", "tps_live", "event_count", "generated_tokens_est",
            "content_chars",
            "summary_model", "resolved_route", "route",
            "has_archived_context", "adjusted_max_tokens", "err_detail",
            "cached_tokens", "cache_hit_ratio", "cache_pct",
            "error", "tb", "url",
        }

        SIMPLE_ONLY_FIELDS = {"last_user", "finished_reason"}
        extra_parts: list[str] = []

        if fields.get("req_id"):
            extra_parts.append(f"REQ={fields['req_id']}")
        if fields.get("route"):
            extra_parts.append(f"ROUTE={fields['route']}")
        if fields.get("model"):
            extra_parts.append(f"MODEL={fields['model']}")

        prompt_tok = fields.get("prompt_tokens")
        completion_tok = fields.get("completion_tokens")
        total_tok = fields.get("total_tokens")
        cached_tok = fields.get("cached_tokens")
        if prompt_tok is not None or completion_tok is not None:
            parts_toks = []
            for tok in [prompt_tok, completion_tok]:
                parts_toks.append(str(tok) if tok is not None else "-")
            if total_tok is not None:
                parts_toks.append(f"total={total_tok}")
            if cached_tok is not None and cached_tok >= 0:
                parts_toks.append(f"cached={cached_tok}")
            extra_parts.append(f"TOKENS={'/'.join(parts_toks)}")

        for k, v in fields.items():
            if k in {"req_id", "route", "model", "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"}:
                continue
            if k not in SAFE_FIELDS and k not in SIMPLE_ONLY_FIELDS:
                continue
            if v is None or isinstance(v, (dict, list)):
                continue
            elif hasattr(v, '__dict__'):
                continue
            else:
                extra_parts.append(f"{k}={v}")

        extra_str = " | ".join(extra_parts) if extra_parts else ""
        full_msg = f"{msg}" + (f" | {extra_str}" if extra_str else "")

        level_map = {"DEBUG": "debug", "INFO": "info", "WARN": "warning", "ERROR": "error"}
        log_level = level_map.get(level, "info")
        server_logger = get_server_logger()
        if server_logger:
            getattr(server_logger, log_level, server_logger.info)(full_msg)
    except Exception:
        pass


# ── Main public API ────────────────────────────────────────────────

def log(level: str, msg: str, **fields: Any) -> None:
    """Main logging dispatcher. Routes to console (ANSI/plain) and file logger."""
    if not _should_log(msg):
        return

    # Write to server debug log file
    _log_to_file(level, msg, **fields)

    rec = {"ts": _ts(), "level": level.upper(), "msg": msg}
    for k, v in fields.items():
        rec[k] = _ensure_serializable(v)

    # ── Always write to NDJSON log (Architecture V2) ──────────────
    _write_json_log(rec)

    # BASIC_PLAIN: write formatted output (for e2e tests, backward compat)
    # The canonical PLAIN log is produced by log-viewer.py from .log.json
    if LOG_MODE == "BASIC_PLAIN":
        from .logging.plain_text import format_plain as fp
        print(fp(rec))
        return

    # Other modes (DEBUG, MEDIUM, BASIC): print rich JSON to stdout
    try:
        from rich import print_json
        print_json(data=rec)
    except Exception:
        print(json.dumps(rec, default=str))


# ── Async helpers for request/response logging ─────────────────────

def _extract_tool_calls_from_messages(messages: Any) -> list[dict] | None:
    """Extract full tool call dicts from messages that contain tool_calls.

    Returns list of dicts with id/type/function keys matching the OpenAI
    tool-call format, suitable for _fmt_tool_calls_yaml.
    """
    if not isinstance(messages, list):
        return None
    tool_calls = []
    for msg in messages:
        if isinstance(msg, dict):
            tc_list = msg.get("tool_calls")
            if isinstance(tc_list, list):
                for tc in tc_list:
                    if isinstance(tc, dict) and "function" in tc:
                        func = tc["function"]
                        if isinstance(func, dict) and func.get("name"):
                            tool_calls.append(tc)
    return tool_calls if tool_calls else None


def _extract_tool_results_from_messages(messages: Any) -> list[dict] | None:
    """Extract tool results from messages with role='tool' or role='function'."""
    import json as _json  # noqa: F811

    if not isinstance(messages, list):
        return None
    results = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("tool", "function"):
            result_entry = {
                "tool_call_id": msg.get("tool_call_id") or msg.get("id"),
                "name": msg.get("name"),
                "content": msg.get("content"),
            }
            if isinstance(result_entry["content"], str):
                try:
                    result_entry["result"] = _json.loads(result_entry["content"])
                except Exception:
                    result_entry["result"] = result_entry["content"]
            else:
                result_entry["result"] = result_entry["content"]
            results.append(result_entry)
    return results if results else None


async def log_request(req: Any) -> None:  # httpx.Request
    """Log an incoming HTTP request (for DEBUG/MEDIUM modes)."""
    from .logging.constants import LOG_MODE as lm

    if lm == "BASIC":
        return

    content_type = req.headers.get("content-type", "")
    body_repr, tool_calls, tool_results = None, None, None

    if req.content:
        if "json" in content_type:
            try:
                body_repr = json.loads(req.content.decode())
                messages = body_repr.get("messages") if isinstance(body_repr, dict) else None
                tool_calls = _extract_tool_calls_from_messages(messages)
                tool_results = _extract_tool_results_from_messages(messages)
            except Exception:
                body_repr = req.content.decode(errors="replace")
        else:
            body_repr = req.content.decode(errors="replace")

    log(
        "DEBUG" if lm == "DEBUG" else "INFO",
        "request_sent", url=str(req.url), method=req.method,
        headers=dict(req.headers), body=body_repr,
        tool_calls=tool_calls, tool_results=tool_results,
    )


async def log_response(r: Any, elapsed_ms: float | None = None) -> None:  # httpx.Response
    """Log an HTTP response (for DEBUG/MEDIUM modes)."""
    from .logging.constants import LOG_MODE as lm

    if lm in {"BASIC", "BASIC_PLAIN"}:
        return

    content_type = r.headers.get("content-type")
    body_repr, model, tool_calls = None, None, None

    try:
        content = r.content or b""
    except Exception:
        content = b""

    if content and _is_json_content_type(content_type):
        try:
            body_repr = await r.json() if hasattr(r, 'json') else json.loads(content)
            if isinstance(body_repr, dict):
                model = body_repr.get("model")
                choices = body_repr.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message", {})
                    if isinstance(msg, dict):
                        tc_list = msg.get("tool_calls")
                        if isinstance(tc_list, list):
                            tool_calls = [tc.get("function", {}).get("name") for tc in tc_list if isinstance(tc, dict) and "function" in tc]
        except Exception:
            body_repr = content.decode("utf-8", errors="replace")

    log(
        "DEBUG" if lm == "DEBUG" else "INFO",
        "response_received", url=str(r.request.url), method=r.request.method,
        status=r.status_code, elapsed_ms=elapsed_ms, headers=dict(r.headers),
        body=body_repr, model=model, tool_calls=tool_calls if tool_calls else None,
    )


async def log_streaming_response(
    r: Any, captured_bytes: bytes, *,
    elapsed_ms: float | None = None, tool_calls: list[str] | None = None,
) -> None:  # httpx.Response
    """Log a streaming response (for DEBUG/MEDIUM modes)."""
    from .logging.constants import LOG_MODE as lm

    if not getattr(lm, '__eq__', lambda x: False)(lm):
        pass

    if "stream" not in str(type(r)):
        return  # skip non-streaming responses

