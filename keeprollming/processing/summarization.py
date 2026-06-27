"""Processing module - Message processing, summarization, and context management.

This module contains utilities for:
- Checking if summarization is needed
- Building cached summaries using cache-append strategy
- Repacking messages with summaries
- Context length management
- Executing summarization actions (moved from endpoints/chat_completions.py)
"""

import os
from ..token_counter import TokenCounter
from typing import Any, Dict, List, Optional, Tuple

from ..config import SUMMARY_CACHE_DIR
from ..logger import log

TOK = TokenCounter()

# Import from rolling_summary module (existing)
from ..rolling_summary import (
    build_messages_from_summary_prefix,
    build_repacked_messages,
    choose_append_until_idx,
    should_summarise,
    split_messages,
    summarize_incremental,
    summarize_middle,
    ensure_repacked_has_user_message,
    _pinned_head_count,
    is_summary_cacheable,
)

# Import from summary_cache module (existing)
from ..summary_cache import (
    conversation_fingerprint,
    find_best_prefix_entry_with_reasons,
    load_cache_entries,
    make_cache_entry,
    save_cache_entry,
)


def check_summarization_needed(
    messages: List[Dict[str, Any]], 
    ctx_len: int, 
    max_tokens: int, 
    token_counter
) -> bool:
    """Check if the conversation needs summarization.
    
    This function determines whether the current conversation has exceeded
    the effective context length and needs to be summarized.
    
    Args:
        messages: List of message dictionaries
        ctx_len: Context length of the model
        max_tokens: Maximum output tokens requested
        token_counter: TokenCounter instance for counting
        
    Returns:
        True if summarization is needed, False otherwise
    """
    plan = should_summarise(
        tok=token_counter,
        messages=messages,
        ctx_eff=ctx_len,
        max_out=max_tokens,
    )
    return plan.should


def build_summary_cache_entry(
    messages: List[Dict[str, Any]],
    user_id: str,
    conv_id: str,
    cache_dir: str,
    fingerprint_msg_count: int = 5,
) -> Optional[Dict[str, Any]]:
    """Build a summary cache entry for the current conversation.
    
    Args:
        messages: List of message dictionaries
        user_id: User identifier
        conv_id: Conversation identifier
        cache_dir: Directory to store cache entries
        fingerprint_msg_count: Number of head messages to include in fingerprint
        
    Returns:
        Cache entry dict or None if not cacheable
    """
    # Check if conversation is cacheable
    if not is_summary_cacheable(messages):
        return None
    
    # Generate fingerprint
    fp = conversation_fingerprint(
        messages=messages,
        user_id=user_id,
        conv_id=conv_id,
        n_head=fingerprint_msg_count,
    )
    
    # Load existing entries
    entries = load_cache_entries(cache_dir, fp, user_id=user_id, conv_id=conv_id)
    
    if not entries:
        return None
    
    # Find best entry
    best_entry, _ = find_best_prefix_entry_with_reasons(entries)
    
    if best_entry is None:
        return None
    
    return best_entry


def repack_messages_with_summary(
    messages: List[Dict[str, Any]],
    summary_text: str,
    append_until_idx: int,
    pinned_head_n: int = 0,
) -> List[Dict[str, Any]]:
    """Repack messages by inserting a summary in the middle.
    
    This implements the cache-append strategy:
    [head messages] + [summary] + [tail messages from append_until_idx]
    
    Args:
        messages: Original list of messages
        summary_text: Text of the generated summary
        append_until_idx: Index from which to start appending remaining messages
        pinned_head_n: Number of pinned head messages (usually system prompts)
        
    Returns:
        Repacked message list with summary inserted
    """
    sys_msg, non_system = split_messages(messages)
    
    # Build repacked messages
    repacked = build_repacked_messages(
        sys_msg=sys_msg,
        non_system=non_system,
        summary=summary_text,
        append_until_idx=append_until_idx,
        pinned_head_n=pinned_head_n,
    )
    
    # Ensure we have at least one user message
    repacked = ensure_repacked_has_user_message(repacked)
    
    return repacked


async def process_messages_for_summarization(
    messages: List[Dict[str, Any]],
    conv_id: str,
    user_id: str,
    route_name: str,
    upstream_model: str,
    summary_model: str,
    cache_dir: str,
    threshold: int,
    desired_start_idx: int,
    pinned_head_n: int,
    req_id: str,
) -> Tuple[List[Dict[str, Any]], Optional[int], Optional[str], Optional[Any]]:
    """Process messages and apply cache-append summarization.
    
    This is the main entry point for summarization processing. It:
    1. Looks up cached summaries
    2. Uses cached summary if found, or creates new one
    3. Repacks messages with the summary
    
    Args:
        messages: Original list of messages
        conv_id: Conversation ID
        user_id: User ID
        route_name: Name of the route
        upstream_model: Upstream model name
        summary_model: Model to use for summarization
        cache_dir: Directory for cache storage
        threshold: Token threshold for summarization
        desired_start_idx: Desired starting index after repacking
        pinned_head_n: Number of pinned head messages
        req_id: Request ID for logging
        
    Returns:
        Tuple of (repacked_messages, summary_info)
    """
    from ..config import SUMMARY_CACHE_ENABLED as cache_enabled
    
    if not cache_enabled:
        return messages, None, None, None
    
    # Generate fingerprint
    fp = conversation_fingerprint(
        messages=messages,
        user_id=user_id,
        conv_id=conv_id,
        n_head=5,  # SUMMARY_CACHE_FINGERPRINT_MSGS
    )
    
    # Load cache entries
    entries = load_cache_entries(cache_dir, fp, user_id=user_id, conv_id=conv_id)
    
    if not entries:
        # No cache - will need to create new summary
        return messages, None, fp, None
    
    # Find best cache entry
    best_entry, reasons = find_best_prefix_entry_with_reasons(entries)
    
    if best_entry is None:
        return messages, None, fp, None
    
    # Use cached summary
    cached_summary = best_entry.get("summary", "")
    
    # Calculate covered_end_idx from the cache entry's range
    covered_end_idx = best_entry.end_idx
    
    append_until_idx = choose_append_until_idx(
        tok=TOK,
        original=messages,
        summary_text=cached_summary,
        covered_end_idx=covered_end_idx,
        threshold=threshold,
        pinned_head_n=pinned_head_n,
    )
    
    # Build repacked messages
    repacked = build_messages_from_summary_prefix(
        original=messages,
        summary_text=cached_summary,
        covered_end_idx=covered_end_idx,
        append_until_idx=append_until_idx,
        pinned_head_n=pinned_head_n,
    )
    
    return repacked, append_until_idx, fp, best_entry


# ── Extracted from keeprollming/endpoints/chat_completions.py ──────────────


def _count_tokens_safe(messages: List[Dict]) -> Optional[int]:
    """Safely count tokens in messages."""
    try:
        return TOK.count_messages(messages)
    except Exception:
        return None


async def _execute_summarization(
    req_id: str,
    messages: List[Dict],
    plan: Any,
    summary_model: str,
    custom_prompt_type: Optional[str],
    custom_prompt_text: Optional[str],
    user_id: str,
    conv_id: str,
    pinned_head_n: int,
    ctx_eff: int,
    is_summary_enabled: bool,
) -> Tuple[List[Dict], bool, int]:
    """Execute the summarization logic with incremental updates."""
    from ..config import SUMMARY_CACHE_FINGERPRINT_MSGS

    repacked_messages = messages
    did_summarize = False
    summary_tokens = 0

    try:
        log(
            "INFO", "summary_needed",
            req_id=req_id,
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
            head_n=plan.head_n,
            tail_n=plan.tail_n,
            middle_count=plan.middle_count,
            summary_model=summary_model,
            repacked_tok_est=plan.repacked_tok_est,
        )

        cache_repacked, append_until_idx, fingerprint, cache_entry = await process_messages_for_summarization(
            messages=messages,
            conv_id=conv_id,
            user_id=user_id,
            route_name="default",
            upstream_model=summary_model,
            summary_model=summary_model,
            cache_dir=SUMMARY_CACHE_DIR,
            threshold=plan.threshold or 0,
            desired_start_idx=plan.head_n,
            pinned_head_n=pinned_head_n,
            req_id=req_id,
        )

        # Lazy imports for monkeypatch compatibility
        from ..rolling_summary import split_messages as _split_msgs
        _, non_system = _split_msgs(messages)

        # Calculate head/tail/middle for potential summarization
        head_n = max(plan.head_n, pinned_head_n)
        tail_n = plan.tail_n
        n = len(non_system)
        middle = non_system[head_n : n - tail_n] if (head_n + tail_n) < n else []

        if cache_repacked is not None and append_until_idx is not None and append_until_idx >= len(non_system) - 1:
            repacked_messages = cache_repacked
            did_summarize = True
            summary_tokens = 0

            log(
                "INFO", "cache_hit_used",
                req_id=req_id,
                cache_entry=fingerprint,
                messages_count=len(cache_repacked),
            )
        elif middle and plan.middle_count > 0:
            from ..rolling_summary import summarize_incremental as _summarize_inc
            summary_text = await _summarize_inc(
                existing_summary="",
                new_messages=middle,
                req_id=req_id,
                summary_model=summary_model,
                prompt_type=custom_prompt_type,
                lang_hint=custom_prompt_text or "english"
            )

            repacked_messages = build_repacked_messages(
                messages, summary_text,
            )
            did_summarize = True
            summary_tokens = _count_tokens_safe(summary_text) or 0

            log(
                "INFO", "incremental_summary_called",
                req_id=req_id,
                messages_count=len(middle),
                summary_model=summary_model,
            )
        else:
            from ..rolling_summary import summarize_middle as _summarize_mid
            summary_text = await _summarize_mid(
                middle, req_id=req_id,
                summary_model=summary_model,
                prompt_type=custom_prompt_type,
                lang_hint=custom_prompt_text or "english"
            )

            repacked_messages = build_repacked_messages(
                messages, summary_text,
            )
            did_summarize = True
            summary_tokens = _count_tokens_safe(summary_text) or 0

        repacked_messages = ensure_repacked_has_user_message(repacked_messages)

        log(
            "INFO", "repacked",
            req_id=req_id,
            did_summarize=did_summarize,
            repacked_msg_count=len(repacked_messages),
            head_n=plan.head_n,
            tail_n=plan.tail_n,
            pinned_head_n=pinned_head_n,
        )
    except Exception as e:
        log("ERROR", "summary_failed_fallback_passthrough", req_id=req_id, err=str(e))
        repacked_messages = messages
        did_summarize = False

    return repacked_messages, did_summarize, summary_tokens
