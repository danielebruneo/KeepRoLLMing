"""Summary module - context summarization with retry logic and chunking."""

# Config constants (re-export for backward compatibility)
# Shared integration seams used by the summary implementation and its tests.
# They live on the canonical package so callers no longer need a compatibility
# wrapper module merely to patch configuration or the upstream client.
import keeprollming.config as _config_mod
import keeprollming.upstream as upstream

# Chunking strategy exports (private)
from .chunking_strategy import (
    _chunk_messages_for_summary,
    _messages_signature,
    _normalize_retry_chunks,
    _should_prechunk_summary_call,
    _should_prechunk_summary_call_async,  # New async version
    _split_oversized_message,
    _split_single_message_for_retry,
    _split_text_preserve_lines,
)
from .config import (
    MAX_HEAD,
    MAX_SUMMARY_BACKEND_ATTEMPTS,
    MAX_TAIL,
    SAFETY_MARGIN_TOK,
    SUMMARY_INSERT_BUDGET_TOK,
    SUMMARY_PIN_FIRST_USER,
    SUMMARY_PROMPT_DIR,
    SUMMARY_PROMPT_TYPE,
    SUMMARY_TEMPERATURE,
)

# Decision engine exports
from .decision_engine import (
    SummarizePlan,
    _pinned_head_count,  # For backward compatibility
    render_messages_for_summary,
    should_summarise,
    split_messages,
)

# Prompt engine exports
from .prompt_engine import (
    DEFAULT_SUMMARY_PROMPTS,
    get_summary_system_prompt,
    load_custom_prompt,
    load_summary_prompt_template,
    render_incremental_summary_prompt,
    render_summary_prompt,
)

# Repacker exports
from .repacker import (
    build_archived_summary_message,
    build_messages_from_summary_prefix,
    build_repacked_messages,
    choose_append_until_idx,
    ensure_repacked_has_user_message,
)

# Summary orchestrator exports (public API)
# Private utilities (internal use only)
from .summary_orchestrator import (
    _request_summary_completion,
    _sanitize_summary_text,
    is_summary_cacheable,
    is_summary_placeholder,
    summarize_incremental,
    summarize_middle,
)

CONFIG = _config_mod.CONFIG
get_ctx_len_for_model = upstream.get_ctx_len_for_model


__all__ = [
    # Config
    "SUMMARY_PROMPT_DIR",
    "SUMMARY_PROMPT_TYPE",
    "SUMMARY_TEMPERATURE",
    "MAX_SUMMARY_BACKEND_ATTEMPTS",
    "SAFETY_MARGIN_TOK",
    "MAX_HEAD",
    "MAX_TAIL",
    "SUMMARY_PIN_FIRST_USER",
    "SUMMARY_INSERT_BUDGET_TOK",
    # Prompt engine
    "load_summary_prompt_template",
    "load_custom_prompt",
    "render_summary_prompt",
    "get_summary_system_prompt",
    "render_incremental_summary_prompt",
    "DEFAULT_SUMMARY_PROMPTS",
    # Decision engine
    "SummarizePlan",
    "should_summarise",
    "split_messages",
    "render_messages_for_summary",
    "_pinned_head_count",  # For backward compatibility
    # Chunking strategy (private functions)
    "_should_prechunk_summary_call",
    "_should_prechunk_summary_call_async",  # New async version
    "_split_text_preserve_lines",
    "_split_oversized_message",
    "_chunk_messages_for_summary",
    "_normalize_retry_chunks",
    "_messages_signature",
    "_split_single_message_for_retry",
    "_sanitize_summary_text",
    "_request_summary_completion",
    "CONFIG",
    "upstream",
    "get_ctx_len_for_model",
    # Summary orchestrator (public API)
    "summarize_middle",
    "summarize_incremental",
    "is_summary_placeholder",
    "is_summary_cacheable",
    # Repacker
    "build_repacked_messages",
    "build_archived_summary_message",
    "build_messages_from_summary_prefix",
    "ensure_repacked_has_user_message",
    "choose_append_until_idx",
]
