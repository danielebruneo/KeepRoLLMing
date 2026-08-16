"""Test-side mini-client/parser for downstream OpenAI-compatible SSE streaming.

Parses raw SSE chunks into canonical test events and validates protocol invariants
as defined in ``docs/STREAMING_PIPELINE_V2_SPEC.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Data classes – canonical test events
# ---------------------------------------------------------------------------

@dataclass
class TestStreamEvent:
    """Base class for all parsed SSE events."""
    __test__ = False
    pass


@dataclass
class TestAssistantTextDelta(TestStreamEvent):
    """Incremental assistant text content."""
    delta: str
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestReasoningTextDelta(TestStreamEvent):
    """Incremental reasoning content."""
    delta: str
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestToolCallDelta(TestStreamEvent):
    """Incremental tool call update."""
    index: int
    id: Optional[str] = None
    name: Optional[str] = None
    arguments_delta: str = ""  # Raw JSON fragment
    is_complete: bool = False
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestToolCallComplete(TestStreamEvent):
    """Tool call fully assembled and validated."""
    index: int
    id: str
    name: str
    arguments_json: str
    arguments_obj: Optional[Dict[str, Any]] = None
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestFinish(TestStreamEvent):
    """Stream finished."""
    reason: str  # stop | length | tool_calls | content_filter | error
    usage: Optional[Dict[str, int]] = None
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestDone(TestStreamEvent):
    """SSE stream terminated. MUST be the last event."""
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestKeepalive(TestStreamEvent):
    """Keepalive marker during long gaps."""
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestError(TestStreamEvent):
    """Fatal streaming error."""
    code: str
    message: str
    _source_chunk_index: int = -1
    __test__ = False


@dataclass
class TestUnknown(TestStreamEvent):
    """Unrecognised event type."""
    raw: str
    _source_chunk_index: int = -1
    __test__ = False


# ---------------------------------------------------------------------------
# Internal helper for tool-call accumulation
# ---------------------------------------------------------------------------

class _ToolCallAccumulator:
    """Accumulates tool_call deltas into a complete tool call dict."""

    def __init__(self) -> None:
        self._calls: Dict[int, Dict[str, Any]] = {}

    def update(self, delta: Dict[str, Any]) -> None:
        idx = delta.get("index", 0)
        call = self._calls.setdefault(idx, {
            "id": delta.get("id"),
            "type": delta.get("type", "function"),
            "function": {"name": "", "arguments": ""},
        })

        if "id" in delta and not call["id"]:
            call["id"] = delta["id"]
        if "type" in delta and call.get("type") == "function":
            call["type"] = delta["type"]

        if "function" in delta:
            fn = delta["function"]
            if "name" in fn and not call["function"]["name"]:
                call["function"]["name"] = fn["name"]
            if "arguments" in fn:
                new_args = fn["arguments"]
                old_args = call["function"]["arguments"]
                if new_args.startswith(old_args):
                    call["function"]["arguments"] = new_args
                else:
                    call["function"]["arguments"] += new_args

    def get_complete(self, chunk_index: int = -1) -> List[TestToolCallComplete]:
        """Return tool calls whose arguments are valid JSON."""
        result: List[TestToolCallComplete] = []
        for idx, call in sorted(self._calls.items()):
            args_json = call["function"]["arguments"]
            try:
                args_obj = json.loads(args_json)
                result.append(TestToolCallComplete(
                    index=idx,
                    id=call["id"] or "",
                    name=call["function"]["name"],
                    arguments_json=args_json,
                    arguments_obj=args_obj,
                    _source_chunk_index=chunk_index,
                ))
            except (json.JSONDecodeError, ValueError):
                result.append(TestToolCallComplete(
                    index=idx,
                    id=call["id"] or "",
                    name=call["function"]["name"],
                    arguments_json=args_json,
                    arguments_obj=None,
                    _source_chunk_index=chunk_index,
                ))
        return result


# ---------------------------------------------------------------------------
# Timestamp footer detection
# ---------------------------------------------------------------------------

_TIMESTAMP_FOOTER_RE = re.compile(
    r"\n*---\n\[?Timestamp: .+?(?: UTC)?",
)


def _count_timestamp_footers(text: str) -> int:
    """Count timestamp footer occurrences in *text*."""
    return len(_TIMESTAMP_FOOTER_RE.findall(text))


# ---------------------------------------------------------------------------
# Tool call flush helper
# ---------------------------------------------------------------------------

def _flush_tool_calls_at_finish(
    accumulator: _ToolCallAccumulator,
    events: List[TestStreamEvent],
    chunk_index: int,
) -> None:
    """Emit accumulated tool calls before the just-appended Finish event.

    Called from _parse_json_chunk right before appending TestFinish.
    This guarantees the canonical order: ToolCallDelta → TestToolCallComplete
    → TestFinish → TestDone.
    """
    complete = accumulator.get_complete(chunk_index=chunk_index)
    # Only emit once (check if we already flushed for this finish).
    has_flushed = any(isinstance(e, TestToolCallComplete) for e in events)
    if not has_flushed:
        events.extend(complete)


def _maybe_flush_tool_calls(
    accumulator: _ToolCallAccumulator,
    events: List[TestStreamEvent],
) -> None:
    """Emit accumulated tool calls at end-of-stream if a Finish event exists."""
    complete = accumulator.get_complete(chunk_index=-1)
    has_flushed = any(isinstance(e, TestToolCallComplete) for e in events)
    if not has_flushed:
        has_finish = any(isinstance(e, TestFinish) for e in events)
        if has_finish:
            events.extend(complete)


# ---------------------------------------------------------------------------
# SSE frame accumulator
# ---------------------------------------------------------------------------

def _split_sse_frames(raw: str) -> List[str]:
    """Split raw text into complete SSE frames.

    An SSE frame ends at ``\\n\\n`` (two consecutive newlines).
    Returns a list of frame strings; the last element may be incomplete.
    """
    return raw.split("\n\n")


def _parse_single_frame(frame: str, accumulator: _ToolCallAccumulator, chunk_index: int) -> List[TestStreamEvent]:
    """Parse one complete SSE frame into events.

    Normalises the frame by stripping CRLF, splitting on blank lines,
    and parsing each ``data:`` line independently.
    """
    # Normalize CRLF → LF
    frame = frame.replace("\r\n", "\n")
    # Split on blank lines (\\n\\n)
    parts = frame.split("\n\n")

    events: List[TestStreamEvent] = []
    for part in parts:
        if not part.strip():
            continue
        # Parse each line inside the part
        for line in part.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Keepalive / comment
            if line.startswith(":") and not line.startswith("data:"):
                events.append(TestKeepalive(_source_chunk_index=chunk_index))
                continue

            # SSE data: line
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    events.append(TestDone(_source_chunk_index=chunk_index))
                    continue
                if payload.startswith("{"):
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        events.append(TestUnknown(raw=frame, _source_chunk_index=chunk_index))
                        continue
                    events.extend(_parse_json_chunk(obj, accumulator, chunk_index))
                continue

            # Raw [DONE] without SSE prefix
            if line == "[DONE]":
                events.append(TestDone(_source_chunk_index=chunk_index))
                continue

    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_sse_events(
    chunks: Sequence[bytes | str],
) -> List[TestStreamEvent]:
    """Parse a sequence of downstream SSE chunks into canonical test events.

    Handles:
    - one SSE frame per chunk
    - multiple SSE frames inside a single chunk (``\\n\\n`` separated)
    - SSE frames split across input chunks

    Parameters
    ----------
    chunks:
        Raw SSE data as ``bytes`` or ``str``.

    Returns
    -------
    list[TestStreamEvent]
        Canonical events in order.
    """
    events: List[TestStreamEvent] = []
    accumulator = _ToolCallAccumulator()

    # Accumulate partial frames across chunks.
    # We use the *chunk index* where a frame *completes* (i.e. where the
    # ``\\n\\n`` delimiter is found).
    partial: str = ""

    for chunk_index, raw in enumerate(chunks):
        s = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
        # Append to the partial buffer
        partial += s

        # Split on blank-line boundaries; keep last element as partial
        parts = partial.split("\n\n")
        # Everything except the last part is a complete frame
        for frame in parts[:-1]:
            if not frame.strip():
                continue
            events.extend(_parse_single_frame(frame, accumulator, chunk_index))

        # Keep the tail as partial for the next iteration
        partial = parts[-1] if parts else ""

    # Flush accumulated tool calls at end-of-stream
    _maybe_flush_tool_calls(accumulator, events)

    # Parse any remaining partial frame (non-empty tail)
    if partial.strip():
        events.extend(_parse_single_frame(partial, accumulator, chunk_index))

    return events


def _parse_json_chunk(
    obj: Dict[str, Any],
    accumulator: _ToolCallAccumulator,
    chunk_index: int = 0,
) -> List[TestStreamEvent]:
    """Parse a single JSON SSE data chunk into events.

    Delta content/tool_calls are parsed BEFORE finish_reason so that
    accumulated tool calls are flushed (as TestToolCallComplete) before
    the Finish event — matching the canonical order:

        ToolCallDelta → TestToolCallComplete → TestFinish → TestDone
    """
    events: List[TestStreamEvent] = []
    choices = obj.get("choices")
    if not choices:
        return events

    for choice in choices:
        delta = choice.get("delta")
        finish_reason = choice.get("finish_reason")
        usage = obj.get("usage")

        # --- delta: content / reasoning / tool_calls ---
        if delta:
            # content
            content = delta.get("content")
            if content is not None and isinstance(content, str):
                events.append(
                    TestAssistantTextDelta(delta=content, _source_chunk_index=chunk_index)
                )

            # reasoning_content
            reasoning = delta.get("reasoning_content")
            if reasoning is not None and isinstance(reasoning, str):
                events.append(
                    TestReasoningTextDelta(delta=reasoning, _source_chunk_index=chunk_index)
                )

            # tool_calls
            tc_list = delta.get("tool_calls")
            if tc_list:
                for tc in tc_list:
                    idx = tc.get("index", 0)
                    delta_id = tc.get("id")
                    fn = tc.get("function", {})
                    name = fn.get("name")
                    args = fn.get("arguments", "")

                    accumulator.update(tc)
                    events.append(
                        TestToolCallDelta(
                            index=idx,
                            id=delta_id,
                            name=name,
                            arguments_delta=args,
                            _source_chunk_index=chunk_index,
                        )
                    )

        # --- finish_reason (after delta) ---
        if finish_reason is not None:
            # Flush accumulated tool calls BEFORE the Finish event.
            _flush_tool_calls_at_finish(accumulator, events, chunk_index)
            events.append(
                TestFinish(reason=str(finish_reason), usage=usage, _source_chunk_index=chunk_index)
            )

    return events


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_assistant_text(events: Sequence[TestStreamEvent]) -> str:
    """Concatenate all assistant text delta values."""
    parts: List[str] = []
    for e in events:
        if isinstance(e, TestAssistantTextDelta):
            parts.append(e.delta)
    return "".join(parts)


def collect_reasoning_text(events: Sequence[TestStreamEvent]) -> str:
    """Concatenate all reasoning content delta values."""
    parts: List[str] = []
    for e in events:
        if isinstance(e, TestReasoningTextDelta):
            parts.append(e.delta)
    return "".join(parts)


def collect_tool_calls(
    events: Sequence[TestStreamEvent],
) -> List[TestToolCallComplete]:
    """Return all complete (valid-json) tool calls."""
    return [
        e for e in events
        if isinstance(e, TestToolCallComplete) and e.arguments_obj is not None
    ]


def collect_finish_events(
    events: Sequence[TestStreamEvent],
) -> List[TestFinish]:
    """Return all finish events."""
    return [e for e in events if isinstance(e, TestFinish)]


# ---------------------------------------------------------------------------
# Protocol validator
# ---------------------------------------------------------------------------

def assert_stream_protocol_valid(
    events: Sequence[TestStreamEvent],
    profile: str = "strict",
) -> None:
    """Validate streaming protocol invariants.

    Raises ``AssertionError`` on the first violation found.

    Parameters
    ----------
    events:
        Parsed test events.
    profile:
        ``"strict"`` or ``"lenient"``.  Strict mode rejects content and
        tool_call in the same assistant delta.
    """

    # --- I2: Done is last ---
    if events and isinstance(events[-1], TestDone):
        pass  # OK
    elif events and not isinstance(events[-1], TestDone):
        raise AssertionError(
            "I2: [DONE] is not the last event"
        )

    # --- I1: Exactly one Finish ---
    finishes = collect_finish_events(events)
    if len(finishes) != 1:
        raise AssertionError(
            f"I1: Expected 1 Finish, got {len(finishes)}"
        )

    finish_idx = next(
        i for i, e in enumerate(events) if isinstance(e, TestFinish)
    )

    # --- I3: Done last (already checked above, but also verify no events after) ---
    # We already verified events[-1] is TestDone, so this is covered.

    # --- I4: No assistant text after finish_reason ---
    for i, e in enumerate(events):
        if i > finish_idx and isinstance(e, TestAssistantTextDelta):
            raise AssertionError(
                f"I4: Assistant text after finish at index {i}"
            )

    # --- I5: No reasoning after finish_reason ---
    for i, e in enumerate(events):
        if i > finish_idx and isinstance(e, TestReasoningTextDelta):
            raise AssertionError(
                f"I5: Reasoning text after finish at index {i}"
            )

    # --- I5b: No tool_call delta after finish_reason ---
    for i, e in enumerate(events):
        if i > finish_idx and isinstance(e, TestToolCallDelta):
            raise AssertionError(
                f"I5b: Tool call delta after finish at index {i}"
            )

    # --- I6: Timestamp appears at most once ---
    text = collect_assistant_text(events)
    ts_count = _count_timestamp_footers(text)
    if ts_count > 1:
        raise AssertionError(
            f"I6: Expected <=1 timestamp footer, got {ts_count}"
        )

    # --- I7: Timestamp before Finish ---
    if ts_count == 1:
        # Find the timestamp position — it's in the last assistant text delta
        # (since the finalizer appends it to the last content chunk).
        last_content_idx = -1
        for i, e in enumerate(events):
            if isinstance(e, TestAssistantTextDelta):
                last_content_idx = i
        if last_content_idx > finish_idx:
            raise AssertionError(
                "I7: Timestamp footer appears after finish"
            )

    # --- I8: Tool call arguments are valid JSON when complete ---
    for e in events:
        if isinstance(e, TestToolCallComplete) and e.arguments_obj is None:
            raise AssertionError(
                f"I8: Tool call at index {e.index} has invalid JSON arguments"
            )

    # --- I9: ToolCallComplete ↔ Finish.reason alignment ---
    has_tool_call_complete = any(
        isinstance(e, TestToolCallComplete) for e in events
    )
    finish_reason = finishes[0].reason if finishes else None
    if has_tool_call_complete and finish_reason != "tool_calls":
        raise AssertionError(
            "I9: Tool call present but finish_reason is not 'tool_calls'"
        )
    if finish_reason == "tool_calls" and not has_tool_call_complete:
        raise AssertionError(
            "I9: finish_reason=tool_calls but no complete tool calls emitted"
        )

    # --- I10: Content and tool_call in same assistant delta (strict) ---
    if profile == "strict":
        # Build a mapping: chunk_index -> set of event types emitted from it
        chunk_types: Dict[int, set] = {}
        for e in events:
            ci = getattr(e, "_source_chunk_index", -1)
            if ci < 0:
                continue
            chunk_types.setdefault(ci, set()).add(type(e).__name__)

        for ci, types in chunk_types.items():
            if "TestAssistantTextDelta" in types and "TestToolCallDelta" in types:
                raise AssertionError(
                    "I10: Content and tool_call emitted from same SSE delta"
                )

    # --- I15: No TestToolCallComplete after Finish ---
    for i, e in enumerate(events):
        if i > finish_idx and isinstance(e, TestToolCallComplete):
            raise AssertionError(
                f"I15: TestToolCallComplete after finish at index {i}"
            )

    # --- I12: [DONE] not duplicated ---
    done_count = sum(1 for e in events if isinstance(e, TestDone))
    if done_count > 1:
        raise AssertionError(
            "I12: Multiple [DONE] events"
        )

    # --- I11: No events after [DONE] ---
    done_idx = next(
        (i for i, e in enumerate(events) if isinstance(e, TestDone)),
        -1,
    )
    if done_idx >= 0 and done_idx != len(events) - 1:
        raise AssertionError(
            "I11: Events found after [DONE]"
        )

    # --- I13: Duplicate timestamp footer already checked at I6 ---

    # --- I14: Tool call without finish_reason="tool_calls" ---
    # Already handled in I9 above.
