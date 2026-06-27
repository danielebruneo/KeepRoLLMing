"""Streaming module - SSE handling, chunk iteration, and streaming transformations.

This module provides:
- SSEStreamHandler: Main handler for SSE streaming with OpenAI compatibility
- ReasoningTransformer: Handles Qwen3.5 reasoning_content transformation
- ToolCallAccumulator: Accumulates incremental tool_calls deltas during streaming
"""

from .sse_handler import SSEStreamHandler, transform_reasoning_to_content
from .reasoning_transformer import (
    inject_placeholder_if_only_reasoning,
    should_transform_delta,
)
from .tool_call_handler import ToolCallAccumulator

__all__ = [
    "SSEStreamHandler",
    "transform_reasoning_to_content",
    "inject_placeholder_if_only_reasoning",
    "should_transform_delta",
    "ToolCallAccumulator",
]
