"""Message chunking strategies for handling oversized summary requests."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..config import SAFETY_MARGIN_TOK


def _estimate_tokens_for_msgs(msgs: List[Dict[str, Any]]) -> int:
    """Rough token estimation for messages (no TokenCounter dependency)."""
    s = ""
    for m in msgs:
        c = m.get("content", "")
        if isinstance(c, str):
            s += c
    return max(1, int(len(s) / 4))


def _should_prechunk_summary_call(
    messages: List[Dict[str, Any]],
    *,
    summary_model: str,
    prompt_type: Optional[str],
    lang_hint: str,
    incremental_existing_summary: str | None = None,
) -> tuple[bool, int, int]:
    """Check if we should pre-chunk messages before summarization.

    Returns: (should_prechunk, est_tokens, threshold)
    
    Note: This function is synchronous and delegates to async functions in summary_orchestrator.py
    for the actual await calls. For backward compatibility with callers that expect sync behavior.
    """
    # Return defaults since we can't do async work here
    return (False, 0, 128)


async def _should_prechunk_summary_call_async(
    messages: List[Dict[str, Any]],
    *,
    summary_model: str,
    prompt_type: Optional[str],
    lang_hint: str,
    incremental_existing_summary: str | None = None,
) -> tuple[bool, int, int]:
    """Async version of _should_prechunk_summary_call that can await upstream calls."""
    from ..upstream import get_ctx_len_for_model
    from .prompt_engine import render_summary_prompt, render_incremental_summary_prompt, get_summary_system_prompt
    
    summary_ctx = await get_ctx_len_for_model(summary_model)
    threshold = max(128, int(summary_ctx) - 512 - int(SAFETY_MARGIN_TOK))
    
    if incremental_existing_summary is None:
        user = render_summary_prompt(render_messages_for_summary(messages), prompt_type=prompt_type, lang_hint=lang_hint)
        body_msgs = [
            {"role": "system", "content": get_summary_system_prompt(prompt_type=prompt_type)},
            {"role": "user", "content": user},
        ]
    else:
        user = render_incremental_summary_prompt(incremental_existing_summary, messages, lang_hint=lang_hint)
        body_msgs = [
            {"role": "system", "content": "You are an assistant that updates a context summary for another model. Do not invent anything. Keep the result compact and faithful."},
            {"role": "user", "content": user},
        ]
    est_tokens = _estimate_tokens_for_msgs(body_msgs)
    return (est_tokens > threshold, est_tokens, threshold)


def _split_text_preserve_lines(text: str, max_chars: int) -> List[str]:
    """Split text into chunks while preserving line breaks."""
    text = text or ""
    if len(text) <= max_chars:
        return [text]
    parts: List[str] = []
    cur = ""
    for line in text.splitlines(True):
        if len(cur) + len(line) <= max_chars:
            cur += line
            continue
        if cur:
            parts.append(cur)
            cur = ""
        while len(line) > max_chars:
            parts.append(line[:max_chars])
            line = line[max_chars:]
        cur = line
    if cur:
        parts.append(cur)
    return [p for p in parts if p]


def _split_oversized_message(msg: Dict[str, Any], max_chars: int) -> List[Dict[str, Any]]:
    """Split a single message into multiple messages if it's too large."""
    content = msg.get("content", "")
    role = msg.get("role")
    
    if isinstance(content, str):
        parts = _split_text_preserve_lines(content, max_chars=max(400, max_chars))
        return [{**msg, "content": part} for part in parts]
    
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                txt = item.get("text")
                if isinstance(txt, str):
                    text_parts.append(txt)
        joined = "\n".join(text_parts)
        parts = _split_text_preserve_lines(joined, max_chars=max(400, max_chars))
        return [{**msg, "content": part, "role": role} for part in parts]
    
    return [msg]


def _chunk_messages_for_summary(
    messages: List[Dict[str, Any]],
    *,
    prompt_type: Optional[str],
    lang_hint: str,
    summary_model_ctx: int,
    incremental_existing_summary: str | None = None,
) -> List[List[Dict[str, Any]]]:
    """Split messages into chunks that fit within the summary model's context."""
    from ..config import SAFETY_MARGIN_TOK
    from .prompt_engine import render_summary_prompt, render_incremental_summary_prompt
    
    threshold = max(128, int(summary_model_ctx) - 512 - int(SAFETY_MARGIN_TOK))
    max_chars_single = max(800, threshold * 4)
    
    expanded: List[Dict[str, Any]] = []
    for m in messages:
        expanded.extend(_split_oversized_message(m, max_chars_single))

    chunks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []

    def est(msgs: List[Dict[str, Any]]) -> int:
        if incremental_existing_summary is None:
            user = render_summary_prompt(render_messages_for_summary(msgs), prompt_type=prompt_type, lang_hint=lang_hint)
            body_msgs = [
                {"role": "system", "content": get_summary_system_prompt(prompt_type=prompt_type)},
                {"role": "user", "content": user},
            ]
        else:
            from .prompt_engine import render_incremental_summary_prompt
            user = render_incremental_summary_prompt(incremental_existing_summary, msgs, lang_hint=lang_hint)
            body_msgs = [
                {"role": "system", "content": "You are an assistant that updates a context summary for another model. Do not invent anything. Keep the result compact and faithful."},
                {"role": "user", "content": user},
            ]
        return _estimate_tokens_for_msgs(body_msgs)

    for msg in expanded:
        candidate = cur + [msg]
        if not cur or est(candidate) <= threshold:
            cur = candidate
            continue
        chunks.append(cur)
        cur = [msg]
    if cur:
        chunks.append(cur)
    return chunks or [expanded]


def _normalize_retry_chunks(
    messages: List[Dict[str, Any]],
    chunks: List[List[Dict[str, Any]]],
) -> tuple[List[List[Dict[str, Any]]], str]:
    """Normalize chunking results for retry scenarios."""
    original_sig = _messages_signature(messages)
    
    if not chunks:
        forced = _force_split_messages(messages)
        return (forced, "empty_chunk_result")
    
    if len(chunks) == 1 and _messages_signature(chunks[0]) == original_sig:
        forced = _force_split_messages(messages)
        if len(forced) > 1:
            return (forced, "forced_split_no_progress")
        return (chunks, "single_chunk_no_progress")
    
    return (chunks, "ok")


def _messages_signature(messages: List[Dict[str, Any]]) -> tuple[int, int, tuple[tuple[str, int], ...]]:
    """Create a signature for messages to detect if chunking made progress."""
    sig: List[tuple[str, int]] = []
    total_chars = 0
    for msg in messages:
        role = str(msg.get("role") or "")
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    val = item.get("text")
                    if isinstance(val, str):
                        parts.append(val)
            text = "\n".join(parts)
        else:
            text = ""
        total_chars += len(text)
        sig.append((role, len(text)))
    return (len(messages), total_chars, tuple(sig))


def _split_single_message_for_retry(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Split a single message into two for retry scenarios."""
    content = msg.get("content", "")
    
    if isinstance(content, str) and len(content) > 1:
        mid = max(1, len(content) // 2)
        left = content[:mid].rstrip() or content[:mid]
        right = content[mid:].lstrip() or content[mid:]
        if left and right:
            return [{**msg, "content": left}, {**msg, "content": right}]
    
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                val = item.get("text")
                if isinstance(val, str):
                    text_parts.append(val)
        joined = "\n".join(text_parts)
        if len(joined) > 1:
            mid = max(1, len(joined) // 2)
            left = joined[:mid].rstrip() or joined[:mid]
            right = joined[mid:].lstrip() or joined[mid:]
            if left and right and left != right:
                return [{**msg, "content": left}, {**msg, "content": right}]
    
    return [msg]


def _force_split_messages(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Force split messages into two chunks."""
    if len(messages) > 1:
        mid = max(1, len(messages) // 2)
        left = messages[:mid]
        right = messages[mid:]
        return [left, right]
    
    if not messages:
        return []
    
    split = _split_single_message_for_retry(messages[0])
    if len(split) > 1:
        return [[split[0]], [split[1]]]
    
    return [messages]


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


def get_summary_system_prompt(prompt_type: Optional[str] = None) -> str:
    """Get system prompt for summarization."""
    from .config import SUMMARY_PROMPT_TYPE
    
    effective_type = (prompt_type or SUMMARY_PROMPT_TYPE or "curated").strip()

    if effective_type == "classic":
        return (
            "You are an assistant that compresses conversations for another model. "
            "Non inventare nulla. Sii fedele, compatto e utile."
        )

    if effective_type == "structured":
        return (
            "You are an assistant that transforms conversations into compact structured state. "
            "Non inventare nulla. Mantieni solo ciò che è utile a continuare la conversazione."
        )

    return (
        "You are a context compaction engine. "
        "Be faithful, compact, structured, and do not invent information."
    )
