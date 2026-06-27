"""Decision engine for summarization - determines when and how to summarize."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..token_counter import TokenCounter


# ---------------------------------------------------------------------
# Configuration imports (lazy to avoid circular imports)
# ---------------------------------------------------------------------
def _get_config():
    """Lazy import of config constants."""
    from .config import (
        SAFETY_MARGIN_TOK,
        SUMMARY_INSERT_BUDGET_TOK,
        MAX_HEAD,
        MAX_TAIL,
        SUMMARY_PIN_FIRST_USER,
    )
    return {
        "SAFETY_MARGIN_TOK": SAFETY_MARGIN_TOK,
        "SUMMARY_INSERT_BUDGET_TOK": SUMMARY_INSERT_BUDGET_TOK,
        "MAX_HEAD": MAX_HEAD,
        "MAX_TAIL": MAX_TAIL,
        "SUMMARY_PIN_FIRST_USER": SUMMARY_PIN_FIRST_USER,
    }


# ---------------------------------------------------------------------
# Decision data structures
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class SummarizePlan:
    """Plan for summarization decision."""
    should: bool
    reason: str
    threshold: int
    prompt_tok_est: int
    head_n: int
    tail_n: int
    middle_count: int
    repacked_tok_est: int
    pinned_head_n: int = 0


# ---------------------------------------------------------------------
# Message splitting utilities
# ---------------------------------------------------------------------

def split_messages(messages: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split system message from non-system messages."""
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if not system_msgs:
        return None, non_system

    merged = ""
    for sm in system_msgs:
        c = sm.get("content", "")
        if isinstance(c, str) and c.strip():
            merged += c.strip() + "\n\n"
    merged = merged.strip()

    return {"role": "system", "content": merged}, non_system


def _estimate_tokens_for_msgs(tok: TokenCounter, msgs: List[Dict[str, Any]]) -> int:
    """Estimate token count for a list of messages."""
    try:
        return tok.count_messages(msgs)
    except Exception:
        # fallback rough estimate
        s = ""
        for m in msgs:
            c = m.get("content", "")
            if isinstance(c, str):
                s += c
        return max(1, int(len(s) / 4))


def _pinned_head_count(non_system: List[Dict[str, Any]], pin_first_user: bool = True) -> int:
    """Count how many messages to pin at the head (usually first user message)."""
    if not pin_first_user:
        return 0
    for idx, msg in enumerate(non_system):
        if msg.get("role") == "user":
            return idx + 1
    return 0


def _estimate_repacked_tokens(
    tok: TokenCounter,
    *,
    sys_msg: Optional[Dict[str, Any]],
    head: List[Dict[str, Any]],
    tail: List[Dict[str, Any]],
    summary_budget_tok: int,
) -> int:
    """Estimate tokens for repacked messages (system + head + tail)."""
    msgs: List[Dict[str, Any]] = []
    if sys_msg:
        msgs.append(sys_msg)
    msgs.extend(head)
    msgs.extend(tail)
    base = _estimate_tokens_for_msgs(tok, msgs)

    # Message overhead + summary budget
    return base + summary_budget_tok + 16


# ---------------------------------------------------------------------
# Head/Tail selection logic
# ---------------------------------------------------------------------

def _choose_head_tail(
    tok: TokenCounter,
    *,
    sys_msg: Optional[Dict[str, Any]],
    non_system: List[Dict[str, Any]],
    threshold: int,
    max_head: int,
    max_tail: int,
    summary_budget_tok: int,
    pinned_head_n: int = 0,
) -> Tuple[int, int, int]:
    """Choose optimal head/tail sizes that fit within threshold.

    Returns: (head_n, tail_n, repacked_tok_est)
    Strategy: maximize kept messages while ensuring non-empty middle to summarize.
    """
    n = len(non_system)
    if n <= 2:
        return (0, 0, _estimate_repacked_tokens(
            tok, sys_msg=sys_msg, head=[], tail=[], summary_budget_tok=summary_budget_tok
        ))

    best: Tuple[int, int, int] | None = None

    # Try larger totals first to maximize kept messages
    min_head = min(max(0, pinned_head_n), n - 1)
    for total in range(min(max_head + max_tail, n), 1, -1):
        # Split total between head and tail (keep at least 1 for tail)
        head_max_this = min(max_head, total - 1)
        for head_n in range(head_max_this, min_head - 1, -1):
            tail_n = total - head_n
            if tail_n < 0 or tail_n > max_tail:
                continue

            # Need a non-empty middle to summarize
            if head_n + tail_n >= n:
                continue
            middle_count = n - head_n - tail_n
            if middle_count <= 0:
                continue

            head = non_system[:head_n]
            tail = non_system[-tail_n:] if tail_n > 0 else []
            est = _estimate_repacked_tokens(
                tok, sys_msg=sys_msg, head=head, tail=tail, summary_budget_tok=summary_budget_tok
            )
            if est <= threshold:
                best = (head_n, tail_n, est)
                return best

    # If nothing fits, fall back to minimal head/tail
    head_n = min(max(min_head, 1 if n > 1 else 0), max_head, n - 1)
    tail_n = min(1, max_tail, n - head_n)
    if head_n + tail_n >= n:
        head_n = min_head
        tail_n = min(1, max_tail, max(0, n - head_n - 1))

    head = non_system[:head_n] if head_n else []
    tail = non_system[-tail_n:] if tail_n else []
    est = _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=head, tail=tail, summary_budget_tok=summary_budget_tok)
    return (head_n, tail_n, est)


# ---------------------------------------------------------------------
# Main decision function
# ---------------------------------------------------------------------

def should_summarise(
    *,
    tok: TokenCounter,
    messages: List[Dict[str, Any]],
    ctx_eff: int,
    max_out: int,
    safety_margin_tok: int = _get_config()["SAFETY_MARGIN_TOK"],
    max_head: int = _get_config()["MAX_HEAD"],
    max_tail: int = _get_config()["MAX_TAIL"],
    summary_insert_budget_tok: int = _get_config()["SUMMARY_INSERT_BUDGET_TOK"],
    pin_first_user: bool = _get_config()["SUMMARY_PIN_FIRST_USER"],
) -> SummarizePlan:
    """Make the summarization decision and choose dynamic head/tail sizes.

    All summarization logic lives here (decision + sizing + accounting).
    
    Args:
        tok: TokenCounter instance for token estimation
        messages: List of conversation messages
        ctx_eff: Effective context length limit
        max_out: Maximum output tokens
        safety_margin_tok: Safety margin below context limit
        max_head: Maximum number of head messages to keep
        max_tail: Maximum number of tail messages to keep
        summary_insert_budget_tok: Budget for summary content in prompt
        pin_first_user: Whether to always pin first user message

    Returns:
        SummarizePlan with decision and sizing information
    """
    config = _get_config()
    threshold = max(256, int(ctx_eff) - int(max_out) - int(safety_margin_tok))
    prompt_tok_est = _estimate_tokens_for_msgs(tok, messages)

    sys_msg, non_system = split_messages(messages)
    n = len(non_system)
    pinned_head_n = _pinned_head_count(non_system, pin_first_user=pin_first_user)

    # Not enough tokens to justify summarizing
    if prompt_tok_est <= threshold:
        return SummarizePlan(
            should=False,
            reason="prompt_within_threshold",
            threshold=threshold,
            prompt_tok_est=prompt_tok_est,
            head_n=0,
            tail_n=0,
            middle_count=max(0, n),
            repacked_tok_est=prompt_tok_est,
            pinned_head_n=pinned_head_n,
        )

    # Need enough non-system messages to have head + middle + tail
    if n < 3:
        return SummarizePlan(
            should=False,
            reason="too_few_messages",
            threshold=threshold,
            prompt_tok_est=prompt_tok_est,
            head_n=0,
            tail_n=0,
            middle_count=max(0, n),
            repacked_tok_est=prompt_tok_est,
            pinned_head_n=pinned_head_n,
        )

    head_n, tail_n, repacked_est = _choose_head_tail(
        tok,
        sys_msg=sys_msg,
        non_system=non_system,
        threshold=threshold,
        max_head=max_head,
        max_tail=max_tail,
        summary_budget_tok=summary_insert_budget_tok,
        pinned_head_n=pinned_head_n,
    )
    middle_count = max(0, n - head_n - tail_n)

    # Only summarize if we actually have a middle to summarize
    if middle_count <= 0:
        return SummarizePlan(
            should=False,
            reason="no_middle",
            threshold=threshold,
            prompt_tok_est=prompt_tok_est,
            head_n=head_n,
            tail_n=tail_n,
            middle_count=0,
            repacked_tok_est=prompt_tok_est,
            pinned_head_n=pinned_head_n,
        )

    # Decision: summarize
    plan = SummarizePlan(
        should=True,
        reason="prompt_exceeds_threshold",
        threshold=threshold,
        prompt_tok_est=prompt_tok_est,
        head_n=head_n,
        tail_n=tail_n,
        middle_count=middle_count,
        repacked_tok_est=repacked_est,
        pinned_head_n=pinned_head_n,
    )

    # Log the decision (lazy import to avoid circular dependency)
    try:
        from ..logger import log
        log(
            "INFO",
            "summary_plan",
            should=True,
            reason="prompt_exceeds_threshold",
            threshold=threshold,
            prompt_tok_est=prompt_tok_est,
            head_n=head_n,
            tail_n=tail_n,
            middle_count=middle_count,
            repacked_tok_est=repacked_est,
            pinned_head_n=pinned_head_n,
        )
    except ImportError:
        pass  # Logging not available, continue without it

    return plan


# ---------------------------------------------------------------------
# Message rendering for summary prompts
# ---------------------------------------------------------------------

def render_messages_for_summary(messages: List[Dict[str, Any]], max_chars: int = 12000) -> str:
    """Render messages as plain text transcript for summary prompt."""
    lines: List[str] = []
    used = 0
    for m in messages:
        role = (m.get("role") or "unknown").upper()
        content = m.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: List[str] = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
            text = "\n".join(parts)
        else:
            text = ""

        text = text.strip()
        if not text:
            continue

        line = f"{role}: {text}"
        lines.append(line)
        used += len(line)
        if used > max_chars:
            lines.append("... (truncated)")
            break
    return "\n".join(lines)
