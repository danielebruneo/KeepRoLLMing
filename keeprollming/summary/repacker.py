"""Repacking logic for building message arrays with summaries."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------
# Configuration imports (lazy)
# ---------------------------------------------------------------------
def _get_config():
    """Lazy import of config constants."""
    from .config import MAX_HEAD, SUMMARY_PIN_FIRST_USER
    return {
        "MAX_HEAD": MAX_HEAD,
        "SUMMARY_PIN_FIRST_USER": SUMMARY_PIN_FIRST_USER,
    }


# ---------------------------------------------------------------------
# Message building utilities
# ---------------------------------------------------------------------

def build_repacked_messages(
    messages: List[Dict[str, Any]],
    summary: str,
    *,
    max_head: int = _get_config()["MAX_HEAD"],
    pin_first_user: bool = _get_config()["SUMMARY_PIN_FIRST_USER"],
) -> List[Dict[str, Any]]:
    """Build a repacked message array with the summary inserted."""
    from .decision_engine import split_messages
    
    sys_msg, non_system = split_messages(messages)
    
    # Determine how many head messages to keep (respecting pin_first_user)
    pinned_head_n = 0
    if pin_first_user:
        for idx, msg in enumerate(non_system):
            if msg.get("role") == "user":
                pinned_head_n = idx + 1
                break
    
    max_head_eff = min(max(1, pinned_head_n), len(non_system) - 1)
    head_n = min(max_head, max_head_eff)
    
    # Build repacked array: system + head + summary + tail
    result: List[Dict[str, Any]] = []
    if sys_msg:
        result.append(sys_msg)
    result.extend(non_system[:head_n])
    
    # Add summary message (as user message for backward compatibility)
    result.append({
        "role": "user",
        "content": f"[ARCHIVED_COMPACT_CONTEXT]\n{summary}\n[/ARCHIVED_COMPACT_CONTEXT]",
    })
    
    # Add tail messages if any remain
    remaining = len(non_system) - head_n
    if remaining > 1:
        result.extend(non_system[-(remaining - 1):])
    
    return result


def build_archived_summary_message(summary: str, *, prefix: bool = True) -> Dict[str, Any]:
    """Build a single message containing the archived summary."""
    content_parts: List[str] = []
    if prefix:
        content_parts.append("[ARCHIVED_COMPACT_CONTEXT]")
    content_parts.append(summary.strip())
    if prefix:
        content_parts.append("[/ARCHIVED_COMPACT_CONTEXT]")
    
    return {
        "role": "user",
        "content": "\n".join(content_parts),
    }


def build_messages_from_summary_prefix(
    messages: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Extract summary from prefix message if present."""
    for msg in reversed(messages):
        content = msg.get("content", "")
        if isinstance(content, str):
            # Look for the start of archived context marker
            idx_start = content.find("[ARCHIVED_COMPACT_CONTEXT]")
            idx_end = content.find("[/ARCHIVED_COMPACT_CONTEXT]")
            if idx_start != -1 and idx_end != -1:
                summary = content[idx_start + len("[ARCHIVED_COMPACT_CONTEXT]"):idx_end].strip()
                return {
                    "role": msg.get("role", "user"),
                    "content": f"[ARCHIVED_COMPACT_CONTEXT]\n{summary}\n[/ARCHIVED_COMPACT_CONTEXT]",
                }
    return None


def ensure_repacked_has_user_message(
    repacked: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Ensure the repacked array has at least one user message."""
    if not repacked:
        return repacked
    
    # Check if we have any user messages
    has_user = any(m.get("role") == "user" for m in repacked)
    if has_user:
        return repacked
    
    # If no user message, convert the last non-system message to user role
    result = list(repacked)
    for i in range(len(result) - 1, -1, -1):
        msg = result[i]
        if msg.get("role") != "system":
            result[i] = {**msg, "role": "user"}
            break
    
    return result


def choose_append_until_idx(
    messages: List[Dict[str, Any]],
    threshold: int,
) -> int:
    """Choose the index up to which we should append messages without summarizing.

    Returns an index such that appending all messages up to (but not including) this index
    stays within the token threshold. This is used for incremental summarization decisions.
    """
    if len(messages) <= 1:
        return len(messages)
    
    # Simple heuristic: count user messages and estimate tokens
    user_count = sum(1 for m in messages if m.get("role") == "user")
    estimated_tokens = int(len(messages) * 50 + user_count * 100)
    
    if estimated_tokens <= threshold:
        return len(messages)
    
    # Binary search to find the optimal cutoff
    lo, hi = 1, len(messages)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        est = int(mid * 50 + sum(1 for m in messages[:mid] if m.get("role") == "user") * 100)
        if est <= threshold:
            lo = mid
        else:
            hi = mid - 1
    
    return max(lo, 1)
