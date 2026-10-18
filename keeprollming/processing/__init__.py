"""Processing module - Message processing, summarization, and context management.

This module provides:
- Summarization orchestration with cache-append strategy
- Message validation and repacking utilities
- Context length management functions
"""

from .message_processor import (
    calculate_token_efficiency,
    count_messages,
    ensure_user_message_present,
    extract_messages_from_request,
    find_last_n_messages,
    split_messages_by_role,
    truncate_messages_to_context,
    validate_messages,
)
from .summarization import (
    _count_tokens_safe,
    _execute_summarization,
    build_summary_cache_entry,
    check_summarization_needed,
    process_messages_for_summarization,
    repack_messages_with_summary,
)

__all__ = [
    # Summarization functions
    "check_summarization_needed",
    "build_summary_cache_entry",
    "repack_messages_with_summary",
    "process_messages_for_summarization",
    "_execute_summarization",
    "_count_tokens_safe",
    # Message processing utilities
    "validate_messages",
    "extract_messages_from_request",
    "count_messages",
    "split_messages_by_role",
    "ensure_user_message_present",
    "truncate_messages_to_context",
    "calculate_token_efficiency",
    "find_last_n_messages",
]
