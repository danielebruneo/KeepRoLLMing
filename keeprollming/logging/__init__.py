"""Re-export all public symbols from logging submodules for backward compatibility.

Any code that does `from ..logger import X` or `from .logger import X` should continue to work.
This module acts as a facade over the modularized package structure.
"""

# ── Constants & globals (from constants.py) ────────────────────────
from __future__ import annotations

# Re-export the global state so code that mutates it still works
from .constants import (
    _PLAIN_CLOSED_REQ_IDS,
    _PLAIN_LAST_REQ_ID,
    ANSI_BLUE,
    ANSI_BOLD,
    ANSI_CYAN,
    ANSI_DIM,
    ANSI_GRAY,
    ANSI_GREEN,
    ANSI_MAGENTA,
    ANSI_RED,
    ANSI_RESET,
    ANSI_YELLOW,
    BASIC_SNIP_CHARS,
    LOG_PLAIN_COLORS,
    LOG_PLAIN_WRAP_WIDTH,
    LOG_SNIP_CHARS,
    LOG_STREAM_CHUNKS,
)

# ── Formatters (from formatters.py) ────────────────────────────────
from .formatters import (
    _fmt_meta,
    _fmt_meta_item,
    _fmt_tokens,
    _fmt_tool_calls_yaml,
    _fmt_tool_result_yaml,
    _fmt_usage,
    _highlight_speaker_chunk,
    _indent_block,
    _normalize_summary_text,
    _wrap_plain_line,
)

# ── Message utilities (from message_utils.py) ──────────────────────
from .message_utils import (
    _extract_tool_calls_from_messages,
    _extract_tool_results_from_messages,
    _snip_obj_active,
    _snip_text_active,
    classify_messages,
    extract_last_user_text,
    get_text_content,
)

# ── Payload summarization (from payload_summary.py) ────────────────
from .payload_summary import summarize_request_payload, summarize_response_payload

# ── Server events (from server_events.py) ──────────────────────────
from .server_events import (
    _extract_connection_target,
    categorize_httpx_error,
    get_debug_logger,
    get_server_logger,
    log_body,
    log_config_error,
    log_config_reload,
    log_connection_error,
    log_exception,
    log_fallback_error,
    log_request_error,
    log_server_event,
    setup_debug_logging,
    setup_server_logging,
)

# ── Snipping & JSON utils (from snipping.py) ───────────────────────
from .snipping import MAX_BODY_CHARS, _is_json_content_type, _snip, snip_json

# ── Filter logging (Phase P6: FilterLogger shim retired) ───────────
# The FilterLogger class and get_filter_logger() are retired. All filters
# now use RuntimeEvent emission via orchestrator/filters/events.py helpers.
# Per-filter-file views can be recreated using Projector selectors on
# source.domain="filter" events.

# ── Main dispatcher (from logger.py — imported at bottom after all deps) ──
# The main `log()` function and `_should_log` are defined in logger.py itself.
# They import from these submodules internally. See logger.py for the full API.


def __getattr__(name: str):
    """Fallback attribute access for any symbol not directly re-exported above."""
    # This handles edge cases where code accesses attributes dynamically
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "_PLAIN_CLOSED_REQ_IDS",
    "_PLAIN_LAST_REQ_ID",
    "ANSI_BLUE",
    "ANSI_BOLD",
    "ANSI_CYAN",
    "ANSI_DIM",
    "ANSI_GRAY",
    "ANSI_GREEN",
    "ANSI_MAGENTA",
    "ANSI_RED",
    "ANSI_RESET",
    "ANSI_YELLOW",
    "BASIC_SNIP_CHARS",
    "LOG_PLAIN_COLORS",
    "LOG_PLAIN_WRAP_WIDTH",
    "LOG_SNIP_CHARS",
    "LOG_STREAM_CHUNKS",
    "_fmt_meta",
    "_fmt_meta_item",
    "_fmt_tokens",
    "_fmt_tool_calls_yaml",
    "_fmt_tool_result_yaml",
    "_fmt_usage",
    "_highlight_speaker_chunk",
    "_indent_block",
    "_normalize_summary_text",
    "_wrap_plain_line",
    "_extract_tool_calls_from_messages",
    "_extract_tool_results_from_messages",
    "_snip_obj_active",
    "_snip_text_active",
    "classify_messages",
    "extract_last_user_text",
    "get_text_content",
    "summarize_request_payload",
    "summarize_response_payload",
    "_extract_connection_target",
    "categorize_httpx_error",
    "get_debug_logger",
    "get_server_logger",
    "log_body",
    "log_config_error",
    "log_config_reload",
    "log_connection_error",
    "log_exception",
    "log_fallback_error",
    "log_request_error",
    "log_server_event",
    "setup_debug_logging",
    "setup_server_logging",
    "MAX_BODY_CHARS",
    "_is_json_content_type",
    "_snip",
    "snip_json",
]
