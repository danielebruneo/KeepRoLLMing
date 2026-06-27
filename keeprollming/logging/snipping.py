"""Snipping (truncation) and JSON serialization utilities."""

from __future__ import annotations

import json
from typing import Any


MAX_BODY_CHARS = 10_000_000  # capture up to 10MB of response bodies for full logging


def _snip(s: str | None, limit: int = MAX_BODY_CHARS) -> str:
    """Snip a string to the given character limit."""
    if s is None:
        return ""
    return s if len(s) <= limit else (s[:limit] + f"... <snip {len(s)-limit} chars>")


def _is_json_content_type(content_type: str | None) -> bool:
    """Check if a content-type header indicates JSON."""
    if not content_type:
        return False
    ct = content_type.split(";", 1)[0].strip().lower()
    return ct == "application/json" or ct.endswith("+json")


def snip_json(obj: Any, max_chars: int = 10_000) -> str:
    """Convert object to JSON string with fallback for non-serializable objects."""

    
    def custom_serializer(o):
        """Custom JSON serializer that handles None and other special types."""
        if o is None:
            return "<UNSET>"  # Sentinel value - not serializable
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    
    try:
        s = json.dumps(obj, ensure_ascii=False, default=custom_serializer)
    except Exception:
        try:
            s = str(obj)
        except Exception:
            s = "<unserializable>"
    if max_chars and len(s) > max_chars:
        return s[:max_chars] + "\u2026"
    return s
