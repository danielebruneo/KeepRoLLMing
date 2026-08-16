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
)
from keeprollming.logger import log
from keeprollming.tool_rewrite import PseudoToolCall, PseudoToolCallParser
from keeprollming.orchestrator.filters.events import emit_tool_rewrite_applied


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

        # Streaming buffer state
        self._xml_buffer: List[bytes] = []
        self._buffering_xml = False
        self._xml_complete = False

    async def process_request(self, request, context):
        return request

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

            emit_tool_rewrite_applied(
                context,
                tool_name=pseudo_call.name,
                original_length=len(content),
                cleaned_length=len(cleaned))

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
