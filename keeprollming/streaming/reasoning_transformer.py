"""Qwen3.5 reasoning content transformation utilities.

This module provides helper functions for handling Qwen3.5's separate reasoning_content
field, including transformation and placeholder injection.
"""

from typing import Dict, Optional


def inject_placeholder_if_only_reasoning(
    has_seen_content: bool, 
    finish_reason: Optional[str],
    placeholder: str = ""
) -> Optional[Dict]:
    """Inject empty content chunk if only reasoning tokens were sent.
    
    Some clients expect at least one content chunk even when the model
    only sends reasoning/thinking tokens. This function creates a synthetic
    chunk with placeholder content in that case.
    
    Args:
        has_seen_content: Whether we've seen regular (non-reasoning) content
        finish_reason: The finish reason from upstream
        placeholder: Placeholder text to inject (default empty string)
        
    Returns:
        Delta dict with empty content if needed, None otherwise
    """
    if has_seen_content or finish_reason is None:
        return None
    
    # Only reasoning tokens were sent - inject placeholder
    return {"content": placeholder}


def should_transform_delta(delta: Dict) -> bool:
    """Determine if a delta needs reasoning_content transformation.
    
    Args:
        delta: Delta dict to check
        
    Returns:
        True if transformation should be applied (has reasoning_content but no content)
    """
    return "reasoning_content" in delta and "content" not in delta
