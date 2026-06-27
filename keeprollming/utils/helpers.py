"""Generic helper utilities.

This module contains small, reusable helper functions that don't fit into
more specialized modules. These are primarily parsing and utility functions
that support the main processing logic.
"""

import re
from typing import Any, Dict, List, Optional, Tuple


def parse_captured_sse_text(sse_text: str) -> Tuple[str, Optional[str], Optional[Dict], int]:
    """Parse raw SSE text to extract assistant message and metadata.
    
    This function is used when direct chunk parsing fails (e.g., for final logging)
    to reconstruct the response from captured SSE data.
    
    Args:
        sse_text: Raw SSE text content
        
    Returns:
        Tuple of (assistant_text, finish_reason, usage_dict, event_count)
    """
    assistant_parts: List[str] = []
    finish_reason: Optional[str] = None
    final_usage: Optional[Dict] = None
    event_count = 0
    buf = sse_text
    
    while True:
        m_sep = re.search(r"\r?\n\r?\n", buf)
        if not m_sep:
            break
        
        block = buf[:m_sep.start()]
        buf = buf[m_sep.end():]
        
        data_lines = []
        for line in block.splitlines():
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        
        if not data_lines:
            continue
        
        payload_sse = "\n".join(data_lines).strip()
        if not payload_sse or payload_sse == "[DONE]":
            continue
        
        event_count += 1
        try:
            obj = __import__("json").loads(payload_sse)
        except Exception:
            continue
        
        if not isinstance(obj, dict):
            continue
        
        if isinstance(obj.get("usage"), dict):
            final_usage = obj.get("usage")
        
        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0] if isinstance(choices[0], dict) else None
            if isinstance(c0, dict):
                for candidate in (c0.get("delta"), c0.get("message")):
                    if isinstance(candidate, dict):
                        piece = candidate.get("content")
                        if isinstance(piece, str) and piece:
                            assistant_parts.append(piece)
                
                fr = c0.get("finish_reason")
                if isinstance(fr, str) and fr:
                    finish_reason = fr
    
    return "".join(assistant_parts).strip(), finish_reason, final_usage, event_count


def contains_archived_context(messages: List[Dict[str, Any]]) -> bool:
    """Check if messages contain archived compact context marker.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        True if any system message contains "[ARCHIVED_COMPACT_CONTEXT]" marker
    """
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str) and "[ARCHIVED_COMPACT_CONTEXT]" in content:
                return True
    return False


def is_tool_orchestration_payload(
    payload: Dict[str, Any], 
    messages: List[Dict[str, Any]]
) -> bool:
    """Determine if request is for memory management (should skip summarization).
    
    Args:
        payload: Full request payload
        messages: List of message dictionaries
        
    Returns:
        True if this is a memory management payload that should bypass summarization
    """
    from ..logger import classify_messages
    
    kind = classify_messages(messages)
    # Memory-management payloads should not carry archived compact context,
    # but tool-enabled chat / web-search requests still benefit from history compaction.
    return kind == "memory"


def extract_last_user_text(messages: List[Dict[str, Any]]) -> str:
    """Extract text from the last user message in a conversation.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        Text content from the last user message, or empty string if none found
    """
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # Handle structured content (text blocks, images, etc.)
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text", "")
    return ""
