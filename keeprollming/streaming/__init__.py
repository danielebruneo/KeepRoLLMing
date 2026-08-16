"""Streaming module - SSE handling, chunk iteration, and streaming transformations.

This module provides:
- SSEStreamHandler: Main handler for SSE streaming with OpenAI compatibility
- ReasoningTransformer: Handles Qwen3.5 reasoning_content transformation
- ToolCallAccumulator: Accumulates incremental tool_calls deltas during streaming
- Events: Canonical streaming event dataclasses
- TimestampFinalizer: tail-buffer finalizer for timestamp deduplication
- StreamFinalizer: streaming finalizer contract (abstract base class)
- OpenAISSESerializer: serializer for canonical events → downstream SSE
"""

from .sse_handler import SSEStreamHandler, transform_reasoning_to_content
from .reasoning_transformer import (
    inject_placeholder_if_only_reasoning,
    should_transform_delta,
)
from .tool_call_handler import ToolCallAccumulator
from .events import (
    AssistantTextDelta,
    Done,
    Error,
    Finish,
    Keepalive,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)
from .finalizers import StreamFinalizer, ToolCallFinalizer
from .serializer import OpenAISSESerializer, serialize_event, serialize_events
from .parser import StreamParser
from .runner import run_stream, collect_stream_events

__all__ = [
    "SSEStreamHandler",
    "transform_reasoning_to_content",
    "inject_placeholder_if_only_reasoning",
    "should_transform_delta",
    "ToolCallAccumulator",
    # Canonical events
    "AssistantTextDelta",
    "Done",
    "Error",
    "Finish",
    "Keepalive",
    "ReasoningTextDelta",
    "StreamEvent",
    "ToolCallComplete",
    "ToolCallDelta",
    # Streaming finalizers
    "TimestampFinalizer",
    "TLSFinalizer",
    "RLSFinalizer",
    "ToolCallFinalizer",
    "ToolRewriteFinalizer",
    "StreamFinalizer",
    # Serializer
    "OpenAISSESerializer",
    "serialize_event",
    "serialize_events",
    # Parser + runner
    "StreamParser",
    "run_stream",
    "collect_stream_events",
]


def __getattr__(name: str):
    """Lazily load finalizers that are owned by filter modules."""
    if name == "TimestampFinalizer":
        from keeprollming.filters.timestamp.stream import TimestampFinalizer

        return TimestampFinalizer
    if name == "ToolRewriteFinalizer":
        from keeprollming.filters.tool_rewrite.stream import ToolRewriteFinalizer

        return ToolRewriteFinalizer
    if name == "TLSFinalizer":
        from keeprollming.filters.tool_loop_stopper.stream import TLSFinalizer

        return TLSFinalizer
    if name == "RLSFinalizer":
        from keeprollming.filters.reasoning_loop_stopper.stream import RLSFinalizer

        return RLSFinalizer
    raise AttributeError(name)
