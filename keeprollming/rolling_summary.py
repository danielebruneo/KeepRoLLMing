from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config import SAFETY_MARGIN_TOK, SUMMARY_MAX_TOKENS, UPSTREAM_BASE_URL
from .logger import log, snip_json
from .upstream import http_client
from .token_counter import TokenCounter

# ----------------------------
# Rolling summary (decision + repack)
# ----------------------------

MAX_HEAD = int(os.getenv("MAX_HEAD", "3"))
MAX_TAIL = int(os.getenv("MAX_TAIL", "3"))

# We need to budget how many tokens the inserted summary will take in the prompt.
# Default: tie it to SUMMARY_MAX_TOKENS (the summary model output cap), conservative.
SUMMARY_INSERT_BUDGET_TOK = int(os.getenv("SUMMARY_INSERT_BUDGET_TOK", str(SUMMARY_MAX_TOKENS)))


@dataclass(frozen=True)
class SummarizePlan:
    should: bool
    reason: str
    threshold: int
    prompt_tok_est: int
    head_n: int
    tail_n: int
    middle_count: int
    repacked_tok_est: int


def split_messages(messages: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
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


def _estimate_repacked_tokens(
    tok: TokenCounter,
    *,
    sys_msg: Optional[Dict[str, Any]],
    head: List[Dict[str, Any]],
    tail: List[Dict[str, Any]],
    summary_budget_tok: int,
) -> int:
    # Estimate tokens for system+head+tail messages, plus:
    # - overhead for inserting a "summary system message"
    # - budget tokens for the summary content itself.
    msgs: List[Dict[str, Any]] = []
    if sys_msg:
        msgs.append(sys_msg)
    msgs.extend(head)
    msgs.extend(tail)
    base = _estimate_tokens_for_msgs(tok, msgs)

    # Message overhead + placeholder content (budgeted separately)
    # We'll add a small fixed overhead for the inserted message itself.
    return base + summary_budget_tok + 16


def _choose_head_tail(
    tok: TokenCounter,
    *,
    sys_msg: Optional[Dict[str, Any]],
    non_system: List[Dict[str, Any]],
    threshold: int,
    max_head: int,
    max_tail: int,
    summary_budget_tok: int,
) -> Tuple[int, int, int]:
    """Return (head_n, tail_n, repacked_tok_est) that fits threshold if possible.

    Strategy: maximize kept messages, bounded by max_head/max_tail, while ensuring a non-empty middle.
    """
    n = len(non_system)
    if n <= 2:
        return (0, 0, _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=[], tail=[], summary_budget_tok=summary_budget_tok))

    best: Tuple[int, int, int] | None = None

    # Try larger totals first
    for total in range(min(max_head + max_tail, n), 1, -1):
        # split total between head and tail
        head_max_this = min(max_head, total - 1)  # keep at least 1 for tail
        for head_n in range(head_max_this, -1, -1):
            tail_n = total - head_n
            if tail_n < 0:
                continue
            if tail_n > max_tail:
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
                tok,
                sys_msg=sys_msg,
                head=head,
                tail=tail,
                summary_budget_tok=summary_budget_tok,
            )
            if est <= threshold:
                best = (head_n, tail_n, est)
                return best

    # If nothing fits, fall back to minimal head/tail (still useful for summarization)
    head_n = min(1, max_head, n - 1)
    tail_n = min(1, max_tail, n - head_n)
    # Ensure we still have a middle
    if head_n + tail_n >= n:
        # squeeze
        head_n = 0
        tail_n = min(1, max_tail, n - 1)

    head = non_system[:head_n] if head_n else []
    tail = non_system[-tail_n:] if tail_n else []
    est = _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=head, tail=tail, summary_budget_tok=summary_budget_tok)
    return (head_n, tail_n, est)


def should_summarise(
    *,
    tok: TokenCounter,
    messages: List[Dict[str, Any]],
    ctx_eff: int,
    max_out: int,
    safety_margin_tok: int = SAFETY_MARGIN_TOK,
    max_head: int = MAX_HEAD,
    max_tail: int = MAX_TAIL,
    summary_insert_budget_tok: int = SUMMARY_INSERT_BUDGET_TOK,
) -> SummarizePlan:
    """Make the summarization decision and choose dynamic head/tail sizes.

    All summarization logic lives here (decision + sizing + accounting).
    """
    threshold = max(256, int(ctx_eff) - int(max_out) - int(safety_margin_tok))
    prompt_tok_est = _estimate_tokens_for_msgs(tok, messages)

    sys_msg, non_system = split_messages(messages)
    n = len(non_system)

    # Not enough to justify summarizing
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
        )

    # Need enough non-system msgs to have head + middle + tail
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
        )

    head_n, tail_n, repacked_est = _choose_head_tail(
        tok,
        sys_msg=sys_msg,
        non_system=non_system,
        threshold=threshold,
        max_head=max_head,
        max_tail=max_tail,
        summary_budget_tok=summary_insert_budget_tok,
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
        )

    return SummarizePlan(
        should=True,
        reason="prompt_exceeds_threshold",
        threshold=threshold,
        prompt_tok_est=prompt_tok_est,
        head_n=head_n,
        tail_n=tail_n,
        middle_count=middle_count,
        repacked_tok_est=repacked_est,
    )


def render_messages_for_summary(messages: List[Dict[str, Any]], max_chars: int = 12000) -> str:
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


async def summarize_middle(middle: List[Dict[str, Any]], req_id: str, summary_model: str) -> str:
    transcript = render_messages_for_summary(middle)

    sys = (
        "Sei un assistente che produce un RIASSUNTO DI CONTESTO per un altro modello.\n"
        "Obiettivo: comprimere la parte centrale preservando fatti, nomi, decisioni, richieste, vincoli e TODO.\n"
        "Regole: non inventare; mantieni lingua coerente (preferisci italiano).\n"
        "Output: breve e denso; bullet points se utile; includi decisioni e TODO.\n"
    )
    user = (
        "Riassumi la parte centrale della conversazione qui sotto.\n\n"
        "=== TRANSCRIPT START ===\n"
        f"{transcript}\n"
        "=== TRANSCRIPT END ===\n\n"
        "RISPOSTA (solo riassunto):"
    )

    body = {
        "model": summary_model,
        "messages": [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        "temperature": 0.4,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "stream": False,
    }

    log(
        "INFO",
        "summary_req",
        req_id=req_id,
        summary_model=summary_model,
        middle_count=len(middle),
        transcript_chars=len(transcript),
        body_json=snip_json(body),
    )

    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    t0 = time.time()
    client = await http_client()
    r = await client.post(url, json=body)
    elapsed_ms = (time.time() - t0) * 1000.0
    r.raise_for_status()

    data = r.json()
    try:
        summary = data["choices"][0]["message"]["content"]
    except Exception:
        summary = ""

    summary = (summary or "").strip() or "(Riassunto non disponibile.)"
    log(
        "INFO",
        "summary_reply",
        req_id=req_id,
        elapsed_ms=round(elapsed_ms, 2),
        usage=data.get("usage"),
        summary_chars=len(summary),
        summary_snip=snip_json(summary, max_chars=min(20000000, 4000)),
        raw_json=snip_json(data),
    )
    return summary


def build_repacked_messages(
    original: List[Dict[str, Any]],
    *,
    summary_text: str,
    head_n: int,
    tail_n: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (repacked_messages, middle_messages_used_for_summary)."""
    sys_msg, non_system = split_messages(original)

    n = len(non_system)
    head_n = max(0, min(int(head_n), n))
    tail_n = max(0, min(int(tail_n), n - head_n))

    head = non_system[:head_n] if head_n else []
    tail = non_system[-tail_n:] if tail_n else []
    middle = non_system[head_n : n - tail_n] if (head_n + tail_n) < n else []

    # If there is nothing to summarize, return original layout (preserve)
    if not middle:
        merged: List[Dict[str, Any]] = []
        if sys_msg:
            merged.append(sys_msg)
        merged.extend(non_system)
        return merged, []

    sys_text = ""
    if sys_msg and isinstance(sys_msg.get("content"), str) and sys_msg["content"].strip():
        sys_text += sys_msg["content"].strip() + "\n\n"

    sum_text = summary_text.strip()

    repacked: List[Dict[str, Any]] = [{"role": "system", "content": sys_text.strip()}]
    repacked.extend(head)
    repacked.append({"role": "system", "content": "riassunto messaggi intermedi:\n{}".format(sum_text)})
    repacked.extend(tail)
    return repacked, middle
