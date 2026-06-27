"""Message extraction and classification utilities for LLM conversations."""

from __future__ import annotations

import json
from typing import Any


def extract_last_user_text(messages: Any) -> str | None:
    """Extract the text content from the last user message in a conversation."""
    if not isinstance(messages, list):
        return None
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                parts = []
                for item in c:
                    if isinstance(item, dict):
                        t = item.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                if parts:
                    return "\n".join(parts)
            return None
    return None


def get_text_content(content: Any) -> str:
    """Extract plain text from a message content field (str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n".join(parts)
    return ""


def classify_messages(messages: Any) -> str:
    """Classify a conversation as 'chat', 'web_search', or 'memory'."""
    if not isinstance(messages, list):
        return "chat"

    system_texts = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            txt = get_text_content(m.get("content"))
            if txt:
                system_texts.append(txt)

    merged = "\n\n".join(system_texts)
    lowered = merged.lower()
    if "# `web_search`:" in merged or "execute immediately without preface" in lowered:
        return "web_search"
    if "don't reply to user, only handle memory" in lowered or "only handle memory" in lowered:
        return "memory"
    return "chat"


def _extract_tool_calls_from_messages(messages: Any) -> list[str] | None:
    """Extract tool call names from messages that contain tool_calls."""
    if not isinstance(messages, list):
        return None

    tool_names = []
    for msg in messages:
        if isinstance(msg, dict):
            tc_list = msg.get("tool_calls")
            if isinstance(tc_list, list):
                for tc in tc_list:
                    if isinstance(tc, dict) and "function" in tc:
                        func = tc["function"]
                        if isinstance(func, dict) and "name" in func:
                            tool_names.append(func["name"])
    return tool_names if tool_names else None


def _extract_tool_results_from_messages(messages: Any) -> list[dict] | None:
    """Extract tool results from messages with role='tool' or role='function'.

    Args:
        messages: List of message dicts

    Returns:
        List of tool result dicts with keys: tool_call_id, name, content/result
    """
    if not isinstance(messages, list):
        return None

    results = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role")
        if role in ("tool", "function"):
            result_entry = {
                "tool_call_id": msg.get("tool_call_id") or msg.get("id"),
                "name": msg.get("name"),
                "content": msg.get("content"),
            }
            # Try to parse content as JSON if it's a string
            if isinstance(result_entry["content"], str):
                try:
                    result_entry["result"] = json.loads(result_entry["content"])
                except Exception:
                    result_entry["result"] = result_entry["content"]
            else:
                result_entry["result"] = result_entry["content"]
            results.append(result_entry)

    return results if results else None


def _snip_text_active(s: str | None, limit: int) -> str:
    """Snip a string to the given character limit."""
    if s is None:
        return ""
    if limit <= 0:
        return s
    return s if len(s) <= limit else (s[:limit] + f"... <snip {len(s)-limit} chars>")


def _snip_obj_active(obj: Any, limit: int) -> Any:
    """Snip an object to the given character limit."""
    try:
        if obj is None:
            return None
        if isinstance(obj, str):
            return _snip_text_active(obj, limit)
        txt = json.dumps(obj, ensure_ascii=False)
        if len(txt) <= limit:
            return obj
        return {"_truncated": True, "preview": _snip_text_active(txt, limit)}
    except Exception:
        return {"_truncated": True, "preview": _snip_text_active(str(obj), limit)}
