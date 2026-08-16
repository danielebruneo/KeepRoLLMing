"""Finalizer contract for Streaming canonical streaming pipeline.

Defines the minimal interface that all stream finalizers must implement.
Finalizers run **after** all stream events are processed but **before**
``Finish`` is emitted into the downstream SSE.

See ``docs/STREAMING_PIPELINE_V2_SPEC.md`` for the full contract.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .events import (
    Finish,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)

# Lazy import to avoid circular dependency
try:
    from ..observability.events import EventSource, RuntimeEvent
    _OBSERVABILITY_AVAILABLE = True
except ImportError:
    _OBSERVABILITY_AVAILABLE = False


class StreamFinalizer(ABC):
    """Base contract for canonical streaming finalizers.

    Every finalizer must implement:

    * ``process_event(event)`` -- called for each ``StreamEvent`` flowing
      through the pipeline.  May buffer, transform, or consume the event.
    * ``finalize()`` -- called once when the upstream stream ends (before
      ``Finish``).  Returns any additional events that must be injected
      into the downstream output (e.g. corrected tail, assembled tool
      calls, flushed buffers).

    Finalizers are ordered by their ``priority`` attribute (lower first).

    Parameters
    ----------
    dispatcher:
        Optional ``EventDispatcher`` for observability instrumentation.

    Return semantics for ``process_event``:

    * ``return [event]`` -- pass-through: the event continues downstream
      unchanged.
    * ``return []`` -- consumed / buffered: no emission yet; the finalizer
      will emit events later (typically from ``finalize()``).
    * ``return [replacement, ...]`` -- replacement: emit one or more
      replacement events in place of the original.
    """

    #: Execution priority -- lower values run first.  Default 50.
    priority: int = 50

    def __init__(self, dispatcher: Any = None) -> None:
        """Initialize finalizer with optional observability dispatcher.

        Parameters
        ----------
        dispatcher:
            Optional EventDispatcher instance.
        """
        self._dispatcher = dispatcher

    def _emit_finalizer_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        level: str = "DEBUG",
    ) -> None:
        """Emit a RuntimeEvent for this finalizer's lifecycle point.

        O2 instrumentation: emits events at process, buffer, flush,
        and finalize lifecycle points.  No-op when dispatcher is None
        or observability is unavailable.
        """
        if not _OBSERVABILITY_AVAILABLE or self._dispatcher is None:
            return
        self._dispatcher.emit(
            RuntimeEvent(
                type=f"streaming.finalizer.{event_type}",
                timestamp_ns=time.time_ns(),
                source=EventSource(
                    domain="streaming",
                    component="finalizer",
                    instance=type(self).__name__,
                ),
                data=data,
                level=level,
            )
        )

    @abstractmethod
    def process_event(self, event: StreamEvent) -> list[StreamEvent]:
        """Process a single ``StreamEvent`` from the pipeline.

        Parameters
        ----------
        event:
            The canonical stream event to process.

        Returns
        -------
        list[StreamEvent]
            Events to emit downstream.  See class docstring for return
            semantics.
        """
        ...

    @abstractmethod
    def finalize(self) -> list[StreamEvent]:
        """Finalize before ``Finish`` is emitted.

        Must be idempotent-safe: implementations that are not idempotent
        should raise ``RuntimeError`` on a second call.

        Returns
        -------
        list[StreamEvent]
            Events to inject into the downstream pipeline (e.g. corrected
            tail buffer, assembled ``ToolCallComplete``, flushed buffers).
            May be empty.
        """
        ...


# ---------------------------------------------------------------------------
# ToolCallFinalizer
# ---------------------------------------------------------------------------


class ToolCallFinalizer(StreamFinalizer):
    """Assemble ToolCallDelta fragments into ToolCallComplete.

    The parser emits ToolCallDelta events only.  This finalizer buffers
    tool-call fragments by index, concatenates arguments in order, and
    emits a single ``ToolCallComplete`` event per index when
    ``finish_reason == "tool_calls"`` is seen (or during ``finalize()``
    if no Finish event arrives).

    Priority: 40 — runs after timestamp (20) but before nudge (50).

    Parameters
    ----------
    flush_valid_only:
        If True (default), only emit ToolCallComplete when the
        arguments are valid JSON.  If False, emit regardless and leave
        ``arguments_obj`` as None for invalid JSON.
    dispatcher:
        Optional EventDispatcher for observability instrumentation.
    """

    priority: int = 40

    def __init__(
        self,
        flush_valid_only: bool = True,
        dispatcher: Any = None,
    ) -> None:
        super().__init__(dispatcher=dispatcher)
        self.flush_valid_only = flush_valid_only
        # index -> {id, name, arguments (list of fragments)}
        self._buffers: Dict[int, Dict[str, Any]] = {}
        self._flushed: bool = False
        self._finalized: bool = False

    def reset(self, preserve_buffer: bool = False) -> None:
        """Reset internal state for recovery between attempts.

        Called by the stream runner between recovery attempts to clear buffered
        tool-call fragments that belong to a failed attempt.

        Parameters
        ----------
        preserve_buffer:
            If True, preserve the assembly buffers (for append_continuation
            strategy). If False (default), clear all buffers.

        Safety
        ------
        * Safe to call before any events are processed.
        * Safe to call multiple times (idempotent).
        * Does not change normal no-recovery behavior.
        * Preserves ToolCallFinalizer ownership of ToolCallComplete.
        """
        if not preserve_buffer:
            self._buffers.clear()
        self._flushed = False
        self._finalized = False

    # ── StreamFinalizer contract ──────────────────────────────────

    def process_event(self, event: StreamEvent) -> list[StreamEvent]:
        """Process a single StreamEvent.

        * ToolCallDelta → buffer, return []
        * Finish(reason="tool_calls") → flush ToolCallComplete events,
          then return [Finish]
        * Finish(reason="stop") with pending tool calls → flush
          ToolCallComplete events, then return [Finish(reason="tool_calls")]
          (I9 alignment: if ToolCallComplete is emitted, Finish must be
          tool_calls)
        * All other events → pass through unchanged
        """
        if isinstance(event, ToolCallDelta):
            self._buffer_delta(event)
            # O2: emit buffer event
            self._emit_finalizer_event("buffer", {
                "tool_call_index": event.index,
                "tool_call_name": event.name or "",
            })
            return []

        if isinstance(event, Finish) and event.reason == "tool_calls":
            complete_events = self._flush_complete()
            if self._flushed:
                # Already flushed at a previous tool_calls finish — don't
                # duplicate.
                return [event]
            self._flushed = True
            return complete_events + [event]

        if isinstance(event, Finish) and event.reason != "tool_calls":
            # Non-tool finish: flush pending complete tool calls if valid.
            if self._buffers:
                if self.flush_valid_only:
                    complete_events = self._flush_valid_only()
                else:
                    complete_events = self._flush_all()
                self._flushed = True
                # I9: if ToolCallComplete was emitted, upgrade Finish to
                # tool_calls so the downstream validator accepts the stream.
                if complete_events:
                    return complete_events + [Finish(reason="tool_calls")]
                # No ToolCallComplete emitted (invalid-only pending calls
                # dropped) — keep original finish reason.
                return [event]
            return [event]

        # Pass through all other events (ToolCallComplete, AssistantTextDelta,
        # ReasoningTextDelta, Done, Keepalive, Error, etc.)
        return [event]

    def finalize(self) -> list[StreamEvent]:
        """Flush any pending tool calls.

        Must be idempotent-safe: second call raises ``RuntimeError``.

        Returns
        -------
        list[StreamEvent]
            ToolCallComplete events for any remaining valid tool calls.
        """
        if self._finalized:
            raise RuntimeError("ToolCallFinalizer.finalize() already called")
        self._finalized = True
        self._flushed = True

        # O2: emit finalize event
        self._emit_finalizer_event("finalize", {
            "pending_buffers": len(self._buffers),
        })

        if not self._buffers:
            return []

        if self.flush_valid_only:
            return self._flush_valid_only()
        return self._flush_all()

    # ── Internal helpers ──────────────────────────────────────────

    def _buffer_delta(self, delta: ToolCallDelta) -> None:
        """Accumulate a ToolCallDelta into the per-index buffer."""
        idx = delta.index
        buf = self._buffers.setdefault(idx, {
            "id": delta.id,
            "name": delta.name,
            "args": [],
            "event_id": delta.event_id,
            "metadata": dict(delta.metadata),
        })
        if delta.id and not buf["id"]:
            buf["id"] = delta.id
        if delta.name and not buf["name"]:
            buf["name"] = delta.name
        if delta.arguments_delta:
            buf["args"].append(delta.arguments_delta)
        if delta.event_id and not buf["event_id"]:
            buf["event_id"] = delta.event_id
        if delta.metadata:
            buf["metadata"].update(delta.metadata)

    def _assemble(self, buf: Dict[str, Any]) -> ToolCallComplete:
        """Build a ToolCallComplete from a buffer dict."""
        args_json = "".join(buf["args"])
        args_obj: Optional[Dict[str, Any]] = None
        try:
            args_obj = json.loads(args_json)
        except (json.JSONDecodeError, ValueError):
            pass
        return ToolCallComplete(
            index=self._buffers.index(buf) if buf in self._buffers.values() else 0,
            id=buf["id"] or "",
            name=buf["name"] or "",
            arguments_json=args_json,
            arguments_obj=args_obj,
            event_id=buf.get("event_id"),
            metadata=dict(buf.get("metadata", {})),
        )

    def _flush_complete(self) -> list[StreamEvent]:
        """Flush all buffers as ToolCallComplete events.

        Respects ``flush_valid_only``: when True, only emits ToolCallComplete
        for buffers whose arguments are valid JSON.
        """
        result: list[StreamEvent] = []
        flushed_keys: list[int] = []
        for idx in sorted(self._buffers.keys()):
            buf = self._buffers[idx]
            args_json = "".join(buf["args"])
            args_obj: Optional[Dict[str, Any]] = None
            try:
                args_obj = json.loads(args_json)
            except (json.JSONDecodeError, ValueError):
                pass
            if self.flush_valid_only and args_obj is None:
                continue
            result.append(ToolCallComplete(
                index=idx,
                id=buf["id"] or "",
                name=buf["name"] or "",
                arguments_json=args_json,
                arguments_obj=args_obj,
                event_id=buf.get("event_id"),
                metadata=dict(buf.get("metadata", {})),
            ))
            flushed_keys.append(idx)
        for key in flushed_keys:
            del self._buffers[key]
        return result

    def _flush_valid_only(self) -> list[StreamEvent]:
        """Flush only buffers whose arguments are valid JSON."""
        result: list[StreamEvent] = []
        flushed_keys: list[int] = []
        for idx in sorted(self._buffers.keys()):
            buf = self._buffers[idx]
            args_json = "".join(buf["args"])
            try:
                args_obj = json.loads(args_json)
                result.append(ToolCallComplete(
                    index=idx,
                    id=buf["id"] or "",
                    name=buf["name"] or "",
                    arguments_json=args_json,
                    arguments_obj=args_obj,
                    event_id=buf.get("event_id"),
                    metadata=dict(buf.get("metadata", {})),
                ))
                flushed_keys.append(idx)
            except (json.JSONDecodeError, ValueError):
                pass
        for key in flushed_keys:
            del self._buffers[key]
        return result

    def _flush_all(self) -> list[StreamEvent]:
        """Flush all buffers regardless of JSON validity."""
        result: list[StreamEvent] = []
        for idx in sorted(self._buffers.keys()):
            buf = self._buffers[idx]
            args_json = "".join(buf["args"])
            args_obj: Optional[Dict[str, Any]] = None
            try:
                args_obj = json.loads(args_json)
            except (json.JSONDecodeError, ValueError):
                pass
            result.append(ToolCallComplete(
                index=idx,
                id=buf["id"] or "",
                name=buf["name"] or "",
                arguments_json=args_json,
                arguments_obj=args_obj,
                event_id=buf.get("event_id"),
                metadata=dict(buf.get("metadata", {})),
            ))
        self._buffers.clear()
        return result
