"""Rolling summary module - backward compatible thin wrapper.

This module is now a thin re-export layer for the new keeprollming.summary package.
All implementation has been moved to keeprollming/summary/*.py modules.

The public API remains unchanged for backward compatibility.
New code should import directly from keeprollming.summary instead.
"""

# Import everything from the summary package for backward compatibility
from .summary.__init__ import (
    # Config constants
    SUMMARY_PROMPT_DIR,
    SUMMARY_PROMPT_TYPE,
    SUMMARY_TEMPERATURE,
    MAX_SUMMARY_BACKEND_ATTEMPTS,
    SAFETY_MARGIN_TOK,
    MAX_HEAD,
    MAX_TAIL,
    SUMMARY_PIN_FIRST_USER,
    SUMMARY_INSERT_BUDGET_TOK,
    
    # Prompt engine
    load_summary_prompt_template,
    load_custom_prompt,
    render_summary_prompt,
    get_summary_system_prompt,
    render_incremental_summary_prompt,
    DEFAULT_SUMMARY_PROMPTS,
    
    # Decision engine
    SummarizePlan,
    should_summarise,
    split_messages,
    render_messages_for_summary,
    _pinned_head_count,  # For backward compatibility
    
    # Chunking strategy (private)
    _should_prechunk_summary_call,
    _should_prechunk_summary_call_async,
    _split_text_preserve_lines,
    _split_oversized_message,
    _chunk_messages_for_summary,
    _normalize_retry_chunks,
    _messages_signature,
    _split_single_message_for_retry,
    
    # Summary orchestrator (public API)
    summarize_middle,
    summarize_incremental,
    is_summary_placeholder,
    is_summary_cacheable,
    
    # Repacker
    build_repacked_messages,
    build_archived_summary_message,
    build_messages_from_summary_prefix,
    ensure_repacked_has_user_message,
    choose_append_until_idx,
)

# Private functions from summary_orchestrator for tests
from .summary.summary_orchestrator import (
    _request_summary_completion,
    _summarize_middle_core,
    _sanitize_summary_text,
)

# Import additional dependencies for backward compatibility with tests
import keeprollming.upstream as upstream  # For monkeypatching in tests
get_ctx_len_for_model = upstream.get_ctx_len_for_model

# Test helper functions (for monkeypatching)
from .summary.decision_engine import _estimate_tokens_for_msgs

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
    "_split_text_preserve_lines",
    "_split_oversized_message",
    "_chunk_messages_for_summary",
    "_normalize_retry_chunks",
    "_messages_signature",
    "_split_single_message_for_retry",
    
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
    
    # Private functions for tests
    "_request_summary_completion",
    "_summarize_middle_core",
    
    # Test utilities (for monkeypatching)
    "get_ctx_len_for_model",
    "_estimate_tokens_for_msgs",
    
    # Config for tests that patch rs.CONFIG
    "CONFIG",
]

# Re-export CONFIG at module level for backward compatibility with tests
import keeprollming.config as _config_mod
CONFIG = getattr(_config_mod, 'CONFIG', None)  # type: ignore