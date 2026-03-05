from __future__ import annotations

from typing import Any, Dict, List

# ----------------------------
# Token counting (best-effort)
# ----------------------------

class TokenCounter:
    """Simple token estimator.

    - Uses tiktoken(cl100k_base) if available
    - Fallback: chars/4
    """
    def __init__(self) -> None:
        self._enc = None
        try:
            import tiktoken  # type: ignore
            self._enc = tiktoken.get_encoding("cl100k_base")
            self.mode = "tiktoken:cl100k_base"
        except Exception:
            self.mode = "chars/4"

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            return len(self._enc.encode(text))
        return max(1, int(len(text) / 4))

    def count_messages(self, messages: List[Dict[str, Any]]) -> int:
        # Simple overhead: ~4 tokens per message + content
        total = 0
        for m in messages:
            total += 4
            content = m.get("content", "")
            if isinstance(content, str):
                total += self.count_text(content)
            elif isinstance(content, list):
                # Multimodal: count only text parts, ignore images
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.count_text(part.get("text", ""))
        return total
