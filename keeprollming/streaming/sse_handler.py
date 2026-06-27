"""SSE (Server-Sent Events) streaming handler.

This module provides the main SSEStreamHandler class that handles all SSE-related
functionality including chunk iteration, transformation, and OpenAI compatibility layer.
"""

import json
import re
from typing import Any, AsyncIterator, Dict, Optional


class SSEStreamHandler:
    """Handles SSE streaming with chunk iteration, buffering, and transformation.
    
    This class encapsulates the complex logic of parsing SSE responses from upstream
    APIs, applying transformations (reasoning_content -> content), handling tool calls,
    and yielding properly formatted chunks to the client.
    
    Attributes:
        request_id: Unique identifier for this request (for logging)
        enable_openai_compat: Whether to apply OpenAI streaming compatibility layer
    """
    
    def __init__(self, request_id: str, enable_openai_compat: bool = True):
        """Initialize SSE stream handler.
        
        Args:
            request_id: Unique request identifier for logging
            enable_openai_compat: Enable OpenAI streaming compatibility layer
        """
        self.request_id = request_id
        self.enable_openai_compat = enable_openai_compat
        
        # State tracking
        self.sse_buffer = ""
        self.assistant_parts: list[str] = []
        self.tool_calls_accumulator: Dict[int, Dict] = {}
        self.finish_reason: Optional[str] = None
        self.final_usage: Optional[Dict] = None
        
        # Streaming state
        self.has_seen_regular_content = False
        self.role_sent = False
        self.stream_event_count = 0
    
    def transform_chunk(self, chunk: bytes) -> bytes:
        """Transform a chunk if reasoning_content transformation is needed.
        
        Args:
            chunk: Raw chunk bytes to potentially transform
            
        Returns:
            Transformed chunk (or original if no transformation needed)
        """
        transformed = chunk
        
        # Try to decode and check if transformation is needed
        try:
            sse_text = chunk.decode("utf-8", errors="replace")
            if not sse_text.startswith("data:"):
                return chunk
                
            data_content = sse_text[5:].strip()
            if not data_content or data_content == "[DONE]":
                return chunk
                
            obj = json.loads(data_content)
            choices = obj.get("choices")
            
            if isinstance(choices, list) and choices:
                c0 = choices[0] if isinstance(choices[0], dict) else None
                if isinstance(c0, dict):
                    delta = c0.get("delta")
                    if isinstance(delta, dict):
                        has_reasoning = "reasoning_content" in delta
                        has_content = "content" in delta and delta.get("content")
                        
                        if has_reasoning and not has_content:
                            # Transform reasoning_content -> content
                            transformed_delta = dict(delta)
                            transformed_delta["content"] = transformed_delta.pop("reasoning_content")
                            choices[0]["delta"] = transformed_delta
                            data_content = json.dumps(obj, separators=(",", ":"))
                            transformed = f"data: {data_content}\n\n".encode("utf-8")
        except Exception:
            # If transformation fails, use original chunk
            pass
        
        return transformed
    
    def parse_sse_block(self, sse_text: str) -> tuple[str, Optional[str], Optional[Dict], int]:
        """Parse raw SSE text to extract assistant message and metadata.
        
        This function is used when direct chunk parsing fails (e.g., for final logging)
        to reconstruct the response from captured SSE data.
        
        Args:
            sse_text: Raw SSE text content
            
        Returns:
            Tuple of (assistant_text, finish_reason, usage_dict, event_count)
        """
        assistant_parts: list[str] = []
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
                obj = json.loads(payload_sse)
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
    
    def ensure_role_sent(self, obj: Dict) -> Optional[bytes]:
        """Ensure role=assistant is sent on first meaningful chunk.
        
        For OpenAI compatibility, we need to emit a synthetic assistant-role
        preface if the first chunk is tool-only or content-less.
        
        Args:
            obj: Parsed SSE event object
            
        Returns:
            Role preface chunk bytes if needed, None otherwise
        """
        if not self.enable_openai_compat or self.role_sent:
            return None
        
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        
        c0 = choices[0]
        if not isinstance(c0, dict):
            return None
        
        delta = c0.get("delta")
        if not isinstance(delta, dict):
            return None
        
        has_tool_calls = bool(delta.get("tool_calls"))
        has_content = isinstance(delta.get("content"), str) and bool(delta.get("content"))
        has_reasoning = isinstance(delta.get("reasoning_content"), str) and bool(delta.get("reasoning_content"))
        
        emit_role_preface = False
        if not self.role_sent:
            if has_tool_calls and not has_content and not has_reasoning and "role" not in delta:
                # First chunk is tool-only, emit role preface
                emit_role_preface = True
            else:
                # Normal case - just set role
                delta["role"] = delta.get("role") or "assistant"
                self.role_sent = True
        
        if emit_role_preface:
            role_chunk_obj = {
                "id": obj.get("id"),
                "object": obj.get("object") or "chat.completion.chunk",
                "created": obj.get("created"),
                "model": obj.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            }
            self.role_sent = True
            return f"data: {json.dumps(role_chunk_obj, separators=(',', ':'))}\n\n".encode("utf-8")
        
        return None


def transform_reasoning_to_content(delta: Dict) -> Dict:
    """Convert reasoning_content → content for OpenAI compatibility.
    
    Qwen3.5 sends thinking/reasoning tokens in a separate field. This function
    transforms them into the standard content field that most clients expect.
    
    Args:
        delta: Delta dict that may contain reasoning_content
        
    Returns:
        Transformed delta with reasoning_content converted to content
    """
    if "reasoning_content" not in delta or "content" in delta:
        return delta
    
    transformed = dict(delta)
    transformed["content"] = transformed.pop("reasoning_content")
    return transformed
