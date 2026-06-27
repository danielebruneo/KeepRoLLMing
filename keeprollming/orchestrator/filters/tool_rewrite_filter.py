"""
ToolRewriteFilter — converts XML pseudo-tool-calls to structured OpenAI format.

Detects XML patterns like <tool_call><function=name>...</tool_call> in model output
and converts them to structured tool_calls in the response.

Non-streaming: modifies response.content + response.tool_calls in Phase 3.
Streaming: XML chunks buffered in Phase 2, rewritten by filter chain in Phase 3,
           then yielded as continuation (tool_calls delta + cleaned content).

Priority=20 — after Summarization (15), before TLS (25).
"""

import json
import re
import uuid
from typing import List, Optional

from keeprollming.orchestrator.filter import (
    Filter,
    FilterConfig,
    FilterExecutionContext,
    Response,
    StreamChunkResult,
    register_filter,
)
from keeprollming.logger import log
from keeprollming.logging import get_filter_logger
from keeprollming.tool_rewrite import PseudoToolCall, PseudoToolCallParser


@register_filter("tool_rewrite")
class ToolRewriteFilter(Filter):
    """Detects XML pseudo-tool-calls and converts to structured format.

    Runs in both streaming (Phase 3 post-streaming) and non-streaming modes.
    Streaming: buffers XML chunks, rewrites to tool_calls, emits continuation.
    """

    priority: int = 20
    name: str = "tool_rewrite"
    supports_streaming: bool = True  # Phase 3 post-streaming rewrite

    def __init__(self, config=None):
        """Initialize ToolRewriteFilter, accepting dict or FilterConfig."""
        if isinstance(config, dict):
            base_config = {"enabled": config.get("enabled", True)}
            self._patterns = config.get("supported_patterns", ["nested", "separate", "function"])
            super().__init__(FilterConfig(**base_config))
        elif config:
            self._patterns = ["nested", "separate", "function"]
            super().__init__(config)
        else:
            self._patterns = ["nested", "separate", "function"]
            super().__init__(FilterConfig(enabled=True))
        try:
            self._logger = get_filter_logger("tool_rewrite")
        except Exception:
            self._logger = None

        # Streaming buffer state
        self._xml_buffer: List[bytes] = []
        self._buffering_xml = False
        self._xml_complete = False

    async def process_request(self, request, context):
        return request

    async def process_stream_chunk(
        self,
        chunk: bytes,
        context: FilterExecutionContext,
    ) -> StreamChunkResult:
        """Buffer XML tool-call chunks and rewrite to structured format.

        This filter does NOT inherit from StreamingFilterBase because it doesn't
        need retry logic — it's a one-pass transformation.

        Args:
            chunk: Raw SSE chunk bytes
            context: Shared execution context

        Returns:
            StreamChunkResult: buffer XML chunks, emit rewritten continuation
        """
        # If we already detected complete XML, just emit
        if self._xml_complete:
            self._xml_complete = False
            self._buffering_xml = False
            self._xml_buffer = []
            return StreamChunkResult(emit=[chunk])

        # Check if this chunk starts an XML tool call
        if not self._buffering_xml:
            content = self._extract_content_from_chunk(chunk)
            if content and self._is_xml_tool_call(content):
                self._buffering_xml = True
                self._xml_buffer = [chunk]
                req_id = context.req_id or "-"
                log("INFO", "tool_rewrite_xml_buffer_start",
                    req_id=req_id, content=content[:100])
                return StreamChunkResult(buffer=None)  # Hold this chunk

        # If buffering, accumulate
        if self._buffering_xml:
            self._xml_buffer.append(chunk)
            content = self._extract_content_from_chunk(chunk)
            if content and self._is_xml_complete(content):
                self._xml_complete = True
                self._buffering_xml = False
                req_id = context.req_id or "-"
                log("INFO", "tool_rewrite_xml_buffer_end",
                    req_id=req_id, buffer_size=len(self._xml_buffer))
                # Rewrite the accumulated buffer and emit continuation
                return self._rewrite_and_emit(context)
            return StreamChunkResult(buffer=None)  # Keep buffering

        # Normal pass-through
        return StreamChunkResult(emit=[chunk])

    def _extract_content_from_chunk(self, chunk: bytes) -> Optional[str]:
        """Extract content string from SSE chunk."""
        try:
            chunk_str = chunk.decode("utf-8")
            if not chunk_str.startswith("data: "):
                return None
            payload_str = chunk_str[6:].strip()
            if payload_str == "[DONE]" or not payload_str:
                return None
            obj = json.loads(payload_str)
            choices = obj.get("choices", [])
            if not choices or not isinstance(choices[0], dict):
                return None
            choice = choices[0]
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                return None
            content = delta.get("content")
            if isinstance(content, str):
                return content
        except Exception:
            pass
        return None

    def _is_xml_tool_call(self, content: str) -> bool:
        """Check if content contains XML pseudo-tool-call."""
        # Quick check for XML-like structure
        if not re.search(r"<[a-zA-Z_][a-zA-Z0-9_-]*(?:\s|=|>)", content):
            return False
        # Check for known patterns
        return bool(
            "<name>" in content or
            "<function=" in content or
            self.TAG_PATTERN.search(content)
        )

    def _is_xml_complete(self, content: str) -> bool:
        """Check if XML tool call is complete (has closing tag)."""
        # Check for common closing patterns
        if "</tool_call>" in content:
            return True
        if "</function>" in content:
            return True
        # Try to find matching closing tag
        tags = re.findall(r"</([a-zA-Z_][a-zA-Z0-9_-]*)>", content)
        open_tags = re.findall(r"<([a-zA-Z_][a-zA-Z0-9_-]*)(?:\s[^>]*)?>", content)
        if tags and open_tags:
            # Simple balance check
            for tag in tags:
                if tag.lower() not in [t.lower() for t in open_tags]:
                    return False
            return True
        return False

    TAG_PATTERN = re.compile(r"<([a-zA-Z_][a-zA-Z0-9_-]*)>([^<]*)</\1>")

    def _rewrite_and_emit(self, context: FilterExecutionContext) -> StreamChunkResult:
        """Rewrite accumulated XML buffer to structured tool_calls.

        Args:
            context: Shared execution context

        Returns:
            StreamChunkResult with rewritten continuation
        """
        req_id = context.req_id or "-"

        # Combine all buffered chunks to extract full content
        combined = b"".join(self._xml_buffer)
        content = self._extract_content_from_chunk(combined) or ""

        # Parse pseudo-tool-call
        parser = PseudoToolCallParser(supported_patterns=self._patterns)
        pseudo_call = parser.parse(content)

        if not pseudo_call:
            log("WARNING", "tool_rewrite_xml_parse_failed",
                req_id=req_id, content=content[:200])
            # Fall back to emitting original buffer
            return StreamChunkResult(emit=list(self._xml_buffer))

        # Build structured tool call
        tool_call = {
            "index": 0,
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": pseudo_call.name,
                "arguments": json.dumps(pseudo_call.arguments),
            },
        }

        # Build continuation message (remove XML from content)
        cleaned = re.sub(
            r'<tool_call>.*?</tool_call>|<function=[^>]*>.*?</function>|<name>.*?</name>',
            '', content, flags=re.DOTALL
        )
        cleaned = cleaned.strip()

        log("INFO", "tool_rewrite_xml_rewritten",
            req_id=req_id,
            tool_name=pseudo_call.name,
            original_len=len(content),
            cleaned_len=len(cleaned))
        if self._logger:
            try:
                self._logger.tool_rewrite_applied(
                    tool_name=pseudo_call.name,
                    original_length=len(content),
                    cleaned_length=len(cleaned))
            except Exception:
                pass

        # Build continuation chunk
        continuation_delta = {"role": "assistant"}
        if cleaned:
            continuation_delta["content"] = cleaned
        continuation_delta["tool_calls"] = [tool_call]

        continuation_obj = {
            "id": context.metadata.get("response_id", ""),
            "created": context.metadata.get("response_created", 0),
            "model": context.metadata.get("upstream_model", ""),
            "choices": [{"index": 0, "delta": continuation_delta}],
        }

        continuation_chunk = (
            f"data: {json.dumps(continuation_obj, separators=(',', ':'))}\n\n"
        ).encode("utf-8")

        # Clear buffer
        self._xml_buffer = []
        self._buffering_xml = False

        return StreamChunkResult(emit=[continuation_chunk])

    async def process_response(
        self, response: Response, context: FilterExecutionContext
    ) -> Response:
        """Detect XML tool calls in response content and convert them (non-streaming)."""
        try:
            content = response.content or ""
            if not content:
                return response

            parser = PseudoToolCallParser(supported_patterns=self._patterns)
            pseudo_call = parser.parse(content)

            if not pseudo_call:
                return response  # No XML tool call found

            # Build structured tool call
            tool_call = {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": pseudo_call.name,
                    "arguments": json.dumps(pseudo_call.arguments),
                },
            }

            # Remove the XML from content (keep surrounding text)
            cleaned = re.sub(
                r'<tool_call>.*?</tool_call>|<function=[^>]*>.*?</function>',
                '', content, flags=re.DOTALL
            )
            cleaned = cleaned.strip()

            log("INFO", "tool_rewrite_applied",
                req_id=context.req_id,
                tool_name=pseudo_call.name,
                original_length=len(content),
                cleaned_length=len(cleaned))
            if self._logger:
                try:
                    self._logger.tool_rewrite_applied(
                        tool_name=pseudo_call.name,
                        original_length=len(content),
                        cleaned_length=len(cleaned))
                except Exception:
                    pass

            # Set both cleaned content and structured tool_calls on the response
            response.content = cleaned
            response.tool_calls = [tool_call]
            # Mark that tool_rewrite has processed this response — ModelNudge
            # can check this flag to avoid re-detecting lazy on cleaned text
            context.metadata["tool_rewrite_done"] = True

        except ImportError:
            log("WARNING", "tool_rewrite_unavailable",
                req_id=context.req_id)
        except Exception as e:
            import traceback
            log("ERROR", "tool_rewrite_filter_error",
                req_id=context.req_id, error=str(e),
                traceback=traceback.format_exc())

        return response
