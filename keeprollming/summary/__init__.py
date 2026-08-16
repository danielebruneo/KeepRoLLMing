"""Summary module - context summarization with retry logic and chunking."""

# Config constants (re-export for backward compatibility)
from .config import (
    SUMMARY_PROMPT_DIR,
    SUMMARY_PROMPT_TYPE,
    SUMMARY_TEMPERATURE,
    MAX_SUMMARY_BACKEND_ATTEMPTS,
    SAFETY_MARGIN_TOK,
    MAX_HEAD,
    MAX_TAIL,
    SUMMARY_PIN_FIRST_USER,
    SUMMARY_INSERT_BUDGET_TOK,
)

# Prompt engine exports
from .prompt_engine import (
    load_summary_prompt_template,
    load_custom_prompt,
    render_summary_prompt,
    get_summary_system_prompt,
    render_incremental_summary_prompt,
    DEFAULT_SUMMARY_PROMPTS,
)

# Decision engine exports
from .decision_engine import (
    SummarizePlan,
    should_summarise,
    split_messages,
    render_messages_for_summary,
    _pinned_head_count,  # For backward compatibility
)

# Chunking strategy exports (private)
from .chunking_strategy import (
    _should_prechunk_summary_call,
    _should_prechunk_summary_call_async,  # New async version
    _split_text_preserve_lines,
    _split_oversized_message,
    _chunk_messages_for_summary,
    _normalize_retry_chunks,
    _messages_signature,
    _split_single_message_for_retry,
)

# Summary orchestrator exports (public API)
from .summary_orchestrator import (
    summarize_middle,
    summarize_incremental,
    is_summary_placeholder,
    is_summary_cacheable,
)

# Repacker exports
from .repacker import (
    build_repacked_messages,
    build_archived_summary_message,
    build_messages_from_summary_prefix,
    ensure_repacked_has_user_message,
    choose_append_until_idx,
)

# Private utilities (internal use only)
from .summary_orchestrator import _request_summary_completion, _sanitize_summary_text

# Shared integration seams used by the summary implementation and its tests.
# They live on the canonical package so callers no longer need a compatibility
# wrapper module merely to patch configuration or the upstream client.
import keeprollming.config as _config_mod
import keeprollming.upstream as upstream

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
