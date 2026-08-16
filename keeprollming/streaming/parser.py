"""SSE → StreamEvent parser for Streaming canonical streaming pipeline.

Parses raw OpenAI-compatible SSE bytes/strings into canonical ``StreamEvent``
objects defined in ``keeprollming/streaming/events.py``.

Design:
- Accumulates partial SSE frames across chunks (same strategy as
  ``tests/helpers/stream_client.py``).
- Handles ``data: [DONE]``, ``: keepalive``, ``choices[0].delta.content``,
  ``choices[0].delta.finish_reason``, and ``choices[0].delta.reasoning_content``.
- ``ToolCallDelta`` is supported as pass-through (optional, trivial).
- ``ToolCallComplete`` is NOT emitted in Phase 1.
- Invalid JSON frames are silently skipped (``continue``).
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Union

from .events import (
    AssistantTextDelta,
    Done,
    Finish,
    Keepalive,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallDelta,
)

# Lazy import to avoid circular dependency
try:
    from ..observability.events import EventSource, RuntimeEvent
    _OBSERVABILITY_AVAILABLE = True
except ImportError:
    _OBSERVABILITY_AVAILABLE = False


# ---------------------------------------------------------------------------
# Internal frame accumulator
# ---------------------------------------------------------------------------


def _split_sse_frames(raw: str) -> List[str]:
    """Split raw text into SSE frames.

    An SSE frame ends at ``\\n\\n`` (two consecutive newlines).
    Returns a list of frame strings; the last element may be incomplete.
    """
    return raw.split("\n\n")


def _parse_single_frame(
    frame: str,
    pending_content: List[str],
    pending_reasoning: List[str],
    pending_tool_calls: Dict[int, Dict[str, Any]],
    pending_usage: "list[Optional[Dict[str, int]]]",
    pending_finish_reason: "list[Optional[str]]",
    pending_envelope: Dict[str, Any],
) -> List[StreamEvent]:
    """Parse one complete SSE frame into StreamEvent objects.

    Parameters
    ----------
    frame:
        A single SSE frame (everything up to ``\\n\\n``).
    pending_content:
        Mutable list accumulating ``delta.content`` across frames.
    pending_reasoning:
        Mutable list accumulating ``delta.reasoning_content`` across frames.
    pending_tool_calls:
        Mutable dict accumulating tool call deltas by index.
    pending_usage:
        Single-element list buffering usage metadata until stream exhaustion.
        The list allows mutation through the call boundary.
    pending_finish_reason:
        Single-element list recording finish_reason when seen. Finish emission
        is deferred to the runner post-loop so that usage arriving after
        finish_reason is captured (Shape B parity).

    Returns
    -------
    list[StreamEvent]
        Events produced by this frame.
    """
    # Normalize CRLF → LF
    frame = frame.replace("\r\n", "\n")
    # Split on blank-line boundaries
    parts = frame.split("\n\n")

    events: List[StreamEvent] = []

    for part in parts:
        if not part.strip():
            continue

        for line in part.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Keepalive / comment
            if line.startswith(":") and not line.startswith("data:"):
                events.append(Keepalive())
                continue

            # SSE data: line
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()

                # [DONE]
                if payload == "[DONE]":
                    events.append(Done())
                    continue

                # JSON payload
                if payload.startswith("{"):
                    try:
                        obj: Dict[str, Any] = json.loads(payload)
                    except (json.JSONDecodeError, ValueError):
                        # Silently skip invalid JSON
                        continue
                    events.extend(
                        _parse_json_chunk(
                            obj,
                            pending_content,
                            pending_reasoning,
                            pending_tool_calls,
                            pending_usage,
                            pending_finish_reason,
                            pending_envelope,
                        )
                    )
                continue

    return events


def _parse_json_chunk(
    obj: Dict[str, Any],
    pending_content: List[str],
    pending_reasoning: List[str],
    pending_tool_calls: Dict[int, Dict[str, Any]],
    pending_usage: "list[Optional[Dict[str, int]]]",
    pending_finish_reason: "list[Optional[str]]",
    pending_envelope: Dict[str, Any],
) -> List[StreamEvent]:
    """Parse a single JSON SSE data chunk into StreamEvents.

    Emits text and reasoning deltas as soon as their upstream SSE frame is
    parsed. ``finish_reason`` remains a terminal barrier handled by the
    runner after recovery-capable finalizers make their decision.

    ``finish_reason`` can appear either at the top level of the choice
    (``choice.finish_reason``) or inside the delta object
    (``delta.finish_reason``).  Both are handled.

    Usage metadata is extracted from the top-level ``obj["usage"]`` field
    (OpenAI-compatible shape). Usage is buffered in ``pending_usage`` (a
    single-element list to allow mutation through the call boundary) until
    stream exhaustion, at which point the runner emits Finish with the
    accumulated usage.

    Latest usage wins: if multiple usage chunks arrive before stream end,
    the last one observed is used.

    Finish emission is deferred to the runner (post-loop) so that usage
    arriving after finish_reason is captured correctly (Shape B parity).
    """
    events: List[StreamEvent] = []

    # Preserve the OpenAI response envelope.  It is not optional metadata:
    # clients use a stable completion id to correlate successive tool turns.
    # The streaming pipeline may assemble several upstream fragments into one event,
    # so retain the most recently supplied values for finalizers and Finish.
    event_id = obj.get("id")
    if isinstance(event_id, str) and event_id:
        pending_envelope["event_id"] = event_id
    for key in ("model", "created"):
        if key in obj:
            pending_envelope.setdefault("metadata", {})[key] = obj[key]

    current_event_id = pending_envelope.get("event_id")
    current_metadata = dict(pending_envelope.get("metadata", {}))

    # Extract usage from top-level obj (OpenAI-compatible shape).
    # Usage may appear in any chunk: content chunks, finish chunks, or
    # usage-only chunks (empty choices).
    # Use a list to allow mutation through the call boundary.
    chunk_usage = obj.get("usage")
    if isinstance(chunk_usage, dict):
        pending_usage[0] = chunk_usage

    choices = obj.get("choices")
    if not choices:
        return events

    for choice in choices:
        delta = choice.get("delta")
        # finish_reason may be at top level or inside delta
        finish_reason = choice.get("finish_reason")
        if finish_reason is None and delta:
            finish_reason = delta.get("finish_reason")

        # --- delta: content / reasoning / tool_calls ---
        # Process delta content BEFORE checking finish_reason so that
        # content and finish_reason in the same chunk are handled correctly.
        if delta:
            # reasoning_content
            reasoning = delta.get("reasoning_content")
            if reasoning is not None and isinstance(reasoning, str):
                events.append(
                    ReasoningTextDelta(
                        delta=reasoning,
                        event_id=current_event_id,
                        metadata=current_metadata,
                    )
                )

            # content.  When both channels share one upstream frame, emit
            # reasoning first; across frames their original arrival order is
            # intentionally preserved.
            content = delta.get("content")
            if content is not None and isinstance(content, str):
                events.append(
                    AssistantTextDelta(
                        delta=content,
                        event_id=current_event_id,
                        metadata=current_metadata,
                    )
                )

            # Keep accepting tool-call deltas until transport exhaustion.
            # Some OpenAI-compatible upstreams place a terminal marker before
            # their final semantic frames.  KRM delays terminal serialization
            # until this parser pass is complete, so dropping those frames
            # would silently remove a later tool call from the same turn.
            tc_list = delta.get("tool_calls")
            if tc_list:
                for tc in tc_list:
                    idx = tc.get("index", 0)
                    delta_id = tc.get("id")
                    fn = tc.get("function", {})
                    name = fn.get("name")
                    args = fn.get("arguments", "")
                    pending_tool_calls.setdefault(idx, {
                        "id": delta_id,
                        "name": name,
                        "arguments": args,
                    })
                    events.append(
                        ToolCallDelta(
                            index=idx,
                            id=delta_id,
                            name=name,
                            arguments_delta=args,
                            event_id=current_event_id,
                            metadata=current_metadata,
                        )
                    )

        # --- finish_reason (defer Finish) ---
        # The runner waits to emit the terminal event so post-finish usage is
        # captured and a recovery finalizer can continue the same SSE stream.
        if finish_reason is not None:
            # Record finish_reason for runner to emit Finish post-loop
            pending_finish_reason[0] = str(finish_reason)

    return events


# ---------------------------------------------------------------------------
# StreamParser
# ---------------------------------------------------------------------------


class StreamParser:
    """Parse upstream SSE bytes/strings → StreamEvent objects.

    Phase 1 supported events:
    - ``AssistantTextDelta``
    - ``Finish``
    - ``Done``
    - ``Keepalive``
    - ``ReasoningTextDelta`` (pass-through)
    - ``ToolCallDelta`` (pass-through)

    Phase 1 does NOT emit ``ToolCallComplete``.

    Parameters
    ----------
    dispatcher:
        Optional ``EventDispatcher`` for observability instrumentation.
        When provided, emits ``streaming.parser.event`` RuntimeEvents
        for each parsed StreamEvent.

    Usage (sync)::

        parser = StreamParser()
        events = parser.parse_sync([chunk1, chunk2])

    Usage (async)::

        parser = StreamParser()
        async for event in parser.parse(upstream_chunks):
            yield event
    """

    def __init__(self, dispatcher: Any = None) -> None:
        """Initialize StreamParser with optional observability dispatcher.

        Parameters
        ----------
        dispatcher:
            Optional EventDispatcher instance. When provided, each
            parsed StreamEvent is emitted as a RuntimeEvent.
        """
        self._dispatcher = dispatcher

    def _emit_parser_events(self, events: List[StreamEvent]) -> None:
        """Emit a RuntimeEvent for each parsed StreamEvent.

        O2 instrumentation: converts StreamEvent → RuntimeEvent and
        emits to the dispatcher.  No-op when dispatcher is None or
        observability is unavailable.
        """
        if not _OBSERVABILITY_AVAILABLE or self._dispatcher is None:
            return
        for event in events:
            event_type = type(event).__name__
            data: Dict[str, Any] = {"event_type": event_type}
            # Add delta length for text events
            if isinstance(event, (AssistantTextDelta, ReasoningTextDelta)):
                data["delta_len"] = len(event.delta)
            # Add finish_reason for Finish events
            if isinstance(event, Finish):
                data["finish_reason"] = event.reason
                if event.usage:
                    data["usage"] = event.usage
            # Add tool call info for ToolCallDelta
            if isinstance(event, ToolCallDelta):
                data["tool_call_index"] = event.index
                data["tool_call_name"] = event.name or ""
            self._dispatcher.emit(
                RuntimeEvent(
                    type="streaming.parser.event",
                    timestamp_ns=time.time_ns(),
                    source=EventSource(domain="streaming", component="parser"),
                    data=data,
                    level="DEBUG",
                )
            )

    def parse_sync(self, chunks: Iterable[Union[bytes, str]]) -> List[StreamEvent]:
        """Parse a sequence of raw SSE chunks into StreamEvent objects.

        Parameters
        ----------
        chunks:
            Raw SSE data as ``bytes`` or ``str``.  Supports multiple SSE
            frames in one chunk and frames split across chunks.

        Returns
        -------
        list[StreamEvent]
            Canonical events in order.
        """
        events: List[StreamEvent] = []
        pending_content: List[str] = []
        pending_reasoning: List[str] = []
        pending_tool_calls: Dict[int, Dict[str, Any]] = {}
        pending_usage: "list[Optional[Dict[str, int]]]" = [None]
        pending_finish_reason: "list[Optional[str]]" = [None]
        pending_envelope: Dict[str, Any] = {}
        partial: str = ""
        done_seen = False

        for raw in chunks:
            s = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            partial += s

            parts = _split_sse_frames(partial)
            for frame in parts[:-1]:
                if not frame.strip():
                    continue
                parsed = _parse_single_frame(
                    frame,
                    pending_content,
                    pending_reasoning,
                    pending_tool_calls,
                    pending_usage,
                    pending_finish_reason,
                    pending_envelope,
                )
                for ev in parsed:
                    if isinstance(ev, Done):
                        done_seen = True
                    else:
                        events.append(ev)
                # O2: Emit RuntimeEvent for each parsed event
                self._emit_parser_events(parsed)
            partial = parts[-1] if parts else ""

        # Flush any remaining accumulated content (in case upstream ended
        # without finish_reason — common in some backends that rely on
        # stream exhaustion).
        # KRM canonical channel order: reasoning before assistant content.
        if pending_reasoning:
            flush_event = ReasoningTextDelta(
                delta="".join(pending_reasoning),
                event_id=pending_envelope.get("event_id"),
                metadata=dict(pending_envelope.get("metadata", {})),
            )
            events.append(flush_event)
            self._emit_parser_events([flush_event])
        if pending_content:
            flush_event = AssistantTextDelta(
                delta="".join(pending_content),
                event_id=pending_envelope.get("event_id"),
                metadata=dict(pending_envelope.get("metadata", {})),
            )
            events.append(flush_event)
            self._emit_parser_events([flush_event])

        # If finish_reason was recorded, emit Finish with accumulated usage.
        # This preserves backward compatibility for standalone parser usage.
        # The runner defers this to post-loop for Shape B parity.
        if pending_finish_reason[0] is not None:
            finish_event = Finish(
                reason=pending_finish_reason[0],
                usage=pending_usage[0],
                event_id=pending_envelope.get("event_id"),
                metadata=dict(pending_envelope.get("metadata", {})),
            )
            events.append(finish_event)
            self._emit_parser_events([finish_event])

        # Done must be last (I2 invariant).
        if done_seen:
            events.append(Done())

        return events

    async def parse(
        self, chunks: AsyncIterator[Union[bytes, str]]
    ) -> AsyncIterator[StreamEvent]:
        """Async generator: parse upstream SSE chunks into StreamEvents.

        Parameters
        ----------
        chunks:
            Async iterator yielding raw SSE bytes/strings.

        Yields
        ------
        StreamEvent
            Canonical events as they are parsed.
        """
        pending_content: List[str] = []
        pending_reasoning: List[str] = []
        pending_tool_calls: Dict[int, Dict[str, Any]] = {}
        pending_usage: "list[Optional[Dict[str, int]]]" = [None]
        pending_finish_reason: "list[Optional[str]]" = [None]
        pending_envelope: Dict[str, Any] = {}
        partial: str = ""

        async for raw in chunks:
            s = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
            partial += s

            parts = _split_sse_frames(partial)
            for frame in parts[:-1]:
                if not frame.strip():
                    continue
                parsed = _parse_single_frame(
                    frame,
                    pending_content,
                    pending_reasoning,
                    pending_tool_calls,
                    pending_usage,
                    pending_finish_reason,
                    pending_envelope,
                )
                for event in parsed:
                    yield event
            partial = parts[-1] if parts else ""

        # Flush remaining accumulated content (KRM canonical channel order: reasoning before assistant content)
        if pending_reasoning:
            yield ReasoningTextDelta(
                delta="".join(pending_reasoning),
                event_id=pending_envelope.get("event_id"),
                metadata=dict(pending_envelope.get("metadata", {})),
            )
        if pending_content:
            yield AssistantTextDelta(
                delta="".join(pending_content),
                event_id=pending_envelope.get("event_id"),
                metadata=dict(pending_envelope.get("metadata", {})),
            )

        # If finish_reason was recorded, emit Finish with accumulated usage.
        # This preserves backward compatibility for standalone parser usage.
        # The runner defers this to post-loop for Shape B parity.
        if pending_finish_reason[0] is not None:
            yield Finish(
                reason=pending_finish_reason[0],
                usage=pending_usage[0],
                event_id=pending_envelope.get("event_id"),
                metadata=dict(pending_envelope.get("metadata", {})),
            )
