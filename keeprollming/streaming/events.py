"""Canonical streaming event model for canonical streaming pipeline.

These dataclasses are the internal representation used by the event pipeline.
They mirror the frozen spec in ``docs/STREAMING_PIPELINE_V2_SPEC.md`` but are
kept minimal — only the types needed for this task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


@dataclass
class StreamEvent:
    """Base class for all streaming events."""

    event_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Delta events
# ---------------------------------------------------------------------------


@dataclass
class AssistantTextDelta(StreamEvent):
    """Incremental assistant text content."""

    delta: str = ""
    event_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasoningTextDelta(StreamEvent):
    """Incremental reasoning content."""

    delta: str = ""
    event_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallDelta(StreamEvent):
    """Incremental tool call update."""

    index: int = 0
    id: Optional[str] = None
    name: Optional[str] = None
    arguments_delta: str = ""
    is_complete: bool = False


@dataclass
class ToolCallComplete(StreamEvent):
    """Tool call fully assembled and validated."""

    index: int = 0
    id: str = ""
    name: str = ""
    arguments_json: str = ""
    arguments_obj: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Terminal events
# ---------------------------------------------------------------------------


@dataclass
class Finish(StreamEvent):
    """Stream finished. Barrier trigger — not serialized immediately."""

    reason: Literal["stop", "length", "tool_calls", "content_filter", "error"] = "stop"
    usage: Optional[Dict[str, int]] = None


@dataclass
class Done(StreamEvent):
    """SSE stream terminated. MUST be the last event."""

    pass


# ---------------------------------------------------------------------------
# Utility events
# ---------------------------------------------------------------------------


@dataclass
class Keepalive(StreamEvent):
    """Keepalive marker during long gaps."""

    pass


@dataclass
class Error(StreamEvent):
    """Fatal streaming error."""

    code: str = ""
    message: str = ""
