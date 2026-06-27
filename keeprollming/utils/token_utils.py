"""Token counting utilities.

This module provides safe token counting wrappers that handle errors gracefully
and provide fallback estimation when exact counts are unavailable.
"""

from typing import Any, Dict, List, Optional, Tuple

from ..token_counter import TokenCounter


# Global token counter instance (singleton pattern)
_TOKEN_COUNTER = TokenCounter()


def count_tokens_safe(messages: List[Dict[str, Any]]) -> Optional[int]:
    """Safely count tokens for a list of messages.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        Token count if successful, None on error
    """
    try:
        return _TOKEN_COUNTER.count_messages(messages)
    except Exception:
        return None


def count_text_tokens_safe(text: str) -> Optional[int]:
    """Safely count tokens for a text string.
    
    Args:
        text: Text to count tokens for
        
    Returns:
        Token count if successful, None on error
    """
    try:
        return _TOKEN_COUNTER.count_text(text)
    except Exception:
        return None


def extract_usage_tokens(usage: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Extract prompt_tokens, completion_tokens, and total_tokens from usage dict.
    
    Args:
        usage: Usage dictionary from upstream response
        
    Returns:
        Tuple of (prompt_tokens, completion_tokens, total_tokens) or (None, None, None) on error
    """
    if not isinstance(usage, dict):
        return (None, None, None)

    def _get_int(name: str) -> Optional[int]:
        try:
            value = usage.get(name)
            return int(value) if value is not None else None
        except Exception:
            return None

    return (_get_int("prompt_tokens"), _get_int("completion_tokens"), _get_int("total_tokens"))


def clamp_max_tokens_for_context(
    requested_max_tokens: Any, 
    ctx_eff: int
) -> int:
    """Clamp max output tokens to prevent context overflow.
    
    This ensures we don't request more output tokens than the model can handle
    while leaving room for the input context.
    
    Args:
        requested_max_tokens: Max tokens requested by client
        ctx_eff: Effective context length of the model
        
    Returns:
        Clamped max output token count
    """
    requested = int(requested_max_tokens) if isinstance(requested_max_tokens, int) and requested_max_tokens > 0 else 900
    hard_cap = max(64, int(ctx_eff) - int(SAFETY_MARGIN_TOK) - 256)
    return max(64, min(requested, hard_cap))


def get_token_counter() -> TokenCounter:
    """Get the global token counter instance.
    
    Returns:
        TokenCounter singleton instance
    """
    return _TOKEN_COUNTER


# ── Extracted from keeprollming/endpoints/chat_completions.py ──────────────

def _clamp_max_out_for_ctx(max_tokens_req, ctx_eff: int) -> int:
    """Clamp max_tokens to fit within context window with safety margin."""
    from ..config import SAFETY_MARGIN_TOK
    max_out = 64
    if max_tokens_req is not None and max_tokens_req > 0:
        max_out = min(max_tokens_req, ctx_eff - SAFETY_MARGIN_TOK)
    return max(max_out, 64)


# ── Extracted from keeprollming/endpoints/streaming_handlers.py ────────────

def _get_completion_tokens(usage) -> int | None:
    """Extract completion_tokens from usage dict, returning None if missing."""
    if usage is None:
        return None
    return usage.get("completion_tokens")


def _get_total_tokens(usage) -> int | None:
    """Extract total_tokens from usage dict, returning None if missing."""
    if usage is None:
        return None
    return usage.get("total_tokens")
