"""Public API for keeprollming logging — thin wrapper over modularized package."""

from __future__ import annotations

from typing import Any

# ── Re-export public symbols from submodules (backward compat) ─────
from .logging.constants import BASIC_SNIP_CHARS, LOG_SNIP_CHARS, LOG_PLAIN_COLORS, LOG_PLAIN_WRAP_WIDTH  # noqa: F401
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

# ── Main public API ────────────────────────────────────────────────

def log(level: str, msg: str, **fields: Any) -> None:
    """Emit a diagnostic RuntimeEvent for transitional internal call sites.

    New runtime paths should use their semantic event producer directly.  This
    helper remains a narrow diagnostic emission primitive, not a formatter or
    file writer.
    """
    try:
        from .observability.events import EventSource, RuntimeEvent
        from .app import get_event_dispatcher

        dispatcher = get_event_dispatcher()
        if dispatcher is not None:
            event_level = "WARN" if level.upper() == "WARN" else level.upper()
            if event_level not in {"TRACE", "DEBUG", "INFO", "BASIC", "WARN", "ERROR"}:
                event_level = "INFO"
            event_type = "diagnostic." + "".join(
                char if char.isalnum() or char == "_" else "_" for char in msg
            )
            dispatcher.emit(RuntimeEvent(
                type=event_type,
                source=EventSource(domain="diagnostic", component="logger"),
                data={key: value for key, value in fields.items() if key not in {"req_id", "trace_id", "span_id"}},
                req_id=fields.get("req_id"), level=event_level,
            ))
    except Exception:
        pass


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
        "DEBUG",
        "request_sent", url=str(req.url), method=req.method,
        headers=dict(req.headers), body=body_repr,
        tool_calls=tool_calls, tool_results=tool_results,
    )


async def log_response(r: Any, elapsed_ms: float | None = None) -> None:  # httpx.Response
    """Log an HTTP response (for DEBUG/MEDIUM modes)."""
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
        "DEBUG",
        "response_received", url=str(r.request.url), method=r.request.method,
        status=r.status_code, elapsed_ms=elapsed_ms, headers=dict(r.headers),
        body=body_repr, model=model, tool_calls=tool_calls if tool_calls else None,
    )


async def log_streaming_response(
    r: Any, captured_bytes: bytes, *,
    elapsed_ms: float | None = None, tool_calls: list[str] | None = None,
) -> None:  # httpx.Response
    """Log a streaming response (for DEBUG/MEDIUM modes)."""
    if "stream" not in str(type(r)):
        return  # skip non-streaming responses
