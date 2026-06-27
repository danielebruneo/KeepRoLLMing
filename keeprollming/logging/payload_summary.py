"""Payload summarization for request/response logging."""

from __future__ import annotations

import json
from typing import Any, Dict

from .message_utils import _snip_text_active, classify_messages, extract_last_user_text


def summarize_request_payload(payload: Any) -> Dict[str, Any]:
    """Summarize a chat completion request payload for logging."""
    if not isinstance(payload, dict):
        return {"kind": "unknown"}

    messages = payload.get("messages")
    summary: Dict[str, Any] = {
        "kind": classify_messages(messages),
        "model": payload.get("model"),
        "stream": bool(payload.get("stream", False)),
        "message_count": len(messages) if isinstance(messages, list) else None,
        "tool_count": len(payload.get("tools")) if isinstance(payload.get("tools"), list) else 0,
        "max_tokens": payload.get("max_tokens"),
    }

    last_user = extract_last_user_text(messages)
    from ..logger import BASIC_SNIP_CHARS
    if last_user:
        summary["last_user"] = _snip_text_active(last_user, BASIC_SNIP_CHARS)

    if isinstance(messages, list):
        summary["has_archived_context"] = any(
            isinstance(m, dict)
            and m.get("role") == "system"
            and "[ARCHIVED_COMPACT_CONTEXT]" in get_text_content(m.get("content"))
            for m in messages
        )
    return summary


def summarize_response_payload(data: Any) -> Dict[str, Any]:
    """Summarize a chat completion response payload for logging."""
    from ..logger import BASIC_SNIP_CHARS

    out: Dict[str, Any] = {}
    if isinstance(data, dict):
        out["model"] = data.get("model")
        out["usage"] = data.get("usage")
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice0 = choices[0] if isinstance(choices[0], dict) else {}
            msg = choice0.get("message") if isinstance(choice0, dict) else None
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    out["assistant_text"] = _snip_text_active(content, BASIC_SNIP_CHARS)
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    out["tool_calls"] = [
                        tc.get("function", {}).get("name")
                        for tc in tool_calls
                        if isinstance(tc, dict)
                    ]
            finish_reason = choice0.get("finish_reason") if isinstance(choice0, dict) else None
            if finish_reason:
                out["finish_reason"] = finish_reason
    return out


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
