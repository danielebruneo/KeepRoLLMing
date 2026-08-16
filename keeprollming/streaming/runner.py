"""canonical streaming runner - orchestrates parser -> finalizers -> serializer.

The runner is a standalone canonical streaming pipeline that:

1. Parses upstream SSE chunks into ``StreamEvent`` objects.
2. Passes each event through finalizers in priority order.
3. On ``Finish`` barrier, runs ``finalize()`` on all finalizers.
4. Serializes finalizer output, then ``Finish``, then ``Done``.

This module does NOT know about route config or ``Pipeline``.
It can be tested and used in isolation.

Usage::

    async for chunk in run_stream(
        upstream_chunks=upstream_stream,
        finalizers=[TimestampFinalizer(template=...)],
        serializer=OpenAISSESerializer(),
    ):
        yield chunk
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Iterable, List, Optional, Union

from .accounting import ExecutionUsage
from .events import (
    AssistantTextDelta,
    Done,
    Finish,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)
from .finalizers import StreamFinalizer
from .parser import StreamParser, _parse_single_frame, _split_sse_frames
from .serializer import OpenAISSESerializer, serialize_event

if TYPE_CHECKING:
    from keeprollming.filters.nudge.stream import RecoveryDecision

# Lazy import to avoid circular dependency
try:
    from ..observability.events import EventSource, RuntimeEvent
    _OBSERVABILITY_AVAILABLE = True
except ImportError:
    _OBSERVABILITY_AVAILABLE = False


# ---------------------------------------------------------------------------
# C2E: Fallback/Exhaustion Constants
# ---------------------------------------------------------------------------

# Default fallback message emitted when recovery is exhausted
DEFAULT_FALLBACK_MESSAGE = "An error occurred while processing your request. Please try again."

# Exception types that trigger fallback on upstream_factory call
UPSTREAM_FACTORY_EXCEPTIONS = (
    Exception,  # Catch all exceptions (including ValueError, RuntimeError, etc.)
)


# ---------------------------------------------------------------------------
# Internal: parse one frame into StreamEvents
# ---------------------------------------------------------------------------


def _parse_frame_into_events(
    frame: str,
    pending_content: List[str],
    pending_reasoning: List[str],
    pending_tool_calls: Dict[int, Dict[str, Any]],
    pending_usage: "list[Optional[Dict[str, int]]]",
    pending_finish_reason: "list[Optional[str]]",
    pending_envelope: Dict[str, Any],
) -> List[StreamEvent]:
    """Parse one complete SSE frame into StreamEvent objects.

    Delegates to ``_parse_single_frame`` from the parser module.

    NOTE: The runner currently uses parser internals
    (``_parse_single_frame``, ``_split_sse_frames``) and maintains its own
    pending state.  This is temporary - Phase 3+ should refactor the runner
    to use ``StreamParser.parse()`` directly.  See
    ``_agent/state/STREAMING_V2_INTEGRATION_PLAN.md`` for the planned refactor.
    """
    return _parse_single_frame(
        frame,
        pending_content,
        pending_reasoning,
        pending_tool_calls,
        pending_usage,
        pending_finish_reason,
        pending_envelope,
    )


# ---------------------------------------------------------------------------
# Internal: sync iterable to async iterator adapter
# ---------------------------------------------------------------------------


async def _sync_to_async(iterable: Iterable) -> AsyncIterator:
    """Convert a sync iterable to an async iterator.

    Used by ``run_stream`` to wrap sync iterables for the unified
    ``_run_core()``.

    Caveat: This only normalizes the iteration interface (sync Iterator
    -> async AsyncIterator). It does NOT make blocking sync iterables
    non-blocking. If the upstream produces sync iterables that block on
    ``__next__()`` (e.g. a generator with I/O), the async loop will still
    block. This preserves existing behavior but does not solve blocking
    sync upstreams.
    """
    for item in iterable:
        yield item


# ---------------------------------------------------------------------------
# Internal: pass events through finalizers + track buffered events
# ---------------------------------------------------------------------------
#
# event-processing mental model (canonical):
#
#   Upstream SSE bytes -> StreamEvent objects -> finalizers in priority order
#   -> serializer -> downstream SSE bytes.
#
#   process_event(event) outcomes:
#     A. return [same_event]   observer/pass-through. event continues.
#     B. return []             suppress immediate output / buffer. event
#                               remains visible to later finalizers.
#                               Finalizer emits via finalize().
#                               REQUIRED for Timestamp + Nudge coexistence.
#     C. return [different...] transform/replacement. original event is
#                               dead. replacement events continue from
#                               next finalizer priority.
#
#   Critical:
#   - "Suppressed for output" does NOT mean "invisible to later finalizers".
#   - Replacement kills the original; buffering does not.
#   - Raw rejected-attempt output is discarded; semantic finalizer-owned
#     buffers may be preserved by explicit recovery strategy.
#
#   Implementation: BFS-style work queue with (event, start_index, suppressed)
#   items. Replacement events start from next finalizer index. No backward
#   processing. ToolCallComplete ownership remains with ToolCallFinalizer.
#
#   WARNING:
#   - Do NOT treat return [] as branch-dead globally.
#   - Do NOT emit replacement events immediately; queue them for downstream.
#   - Output only receives events that completed downstream processing.
#
# ---------------------------------------------------------------------------


def _pass_through_finalizers_with_buffer_tracking(
    events: List[StreamEvent],
    finalizers_by_priority: List[StreamFinalizer],
) -> tuple[List[StreamEvent], bool]:
    """Pass events through finalizers and track if any were buffered.

    Supports finalizer output chaining with true replacement semantics:

    - Pass-through: finalizer returns [same_event] -> continue to next
    - Buffer/consume: finalizer returns [] -> event is suppressed for
      immediate output, but later finalizers still observe it. The
      finalizer will emit content in finalize().
    - Transform/replacement: finalizer returns [different_event...] ->
      original event is suppressed, produced events continue from next
      finalizer index

    Events are NOT emitted when a finalizer returns them. Returned events
    are queued for downstream processing. Only events that finish the
    remaining finalizer chain (all subsequent finalizers pass through or
    buffer) and are NOT suppressed are emitted.

    Returns a tuple of (output_events, any_buffered) where ``any_buffered``
    is True if any finalizer returned [] (buffered/consumed) for at least
    one event in the input list.

    Key semantics:
    - suppressed=True: event reached end but should NOT be emitted (some
      finalizer buffered/consumed it for later emission via finalize())
    - suppressed=False: event reached end unchanged, emit it
    - Replacement: original event is dead, produced events are fresh and
      start from the next finalizer index
    """
    if not finalizers_by_priority:
        return list(events), False

    any_buffered = False
    output: List[StreamEvent] = []

    # Work queue: each item is (event, start_index, suppressed)
    # - event: the event to process
    # - start_index: the index of the next finalizer to process
    # - suppressed: True if a previous finalizer consumed/buffered this
    #   event, meaning it should NOT be emitted even if it reaches the end
    work_queue: List[tuple[StreamEvent, int, bool]] = [
        (ev, 0, False) for ev in events
    ]

    while work_queue:
        ev, start_idx, suppressed = work_queue.pop(0)

        transformed = False

        for i in range(start_idx, len(finalizers_by_priority)):
            fin = finalizers_by_priority[i]
            produced = fin.process_event(ev)

            # A live Nudge observes the original delta for terminal lazy
            # detection while preserving its downstream timing.  Timestamp
            # still records its tail, but must not make this delta buffered:
            # it will contribute only the final footer at the finish barrier.
            if (
                type(fin).__name__ == "TimestampFinalizer"
                and isinstance(ev, AssistantTextDelta)
                and any(
                    type(later).__name__ == "NudgeContinuationFinalizer"
                    for later in finalizers_by_priority[i + 1:]
                )
            ):
                if not any(
                    type(later).__name__ == "NudgeContinuationFinalizer"
                    and getattr(later, "stream_deltas", False)
                    for later in finalizers_by_priority[i + 1:]
                ):
                    # Standalone finalizer mode keeps the historic
                    # finalize-owned behaviour.
                    suppressed = True
                    any_buffered = True
                continue

            if produced == []:
                # Buffer/consume: event is suppressed for immediate output,
                # but later finalizers still observe it.
                suppressed = True
                any_buffered = True
                continue

            if produced is not None and len(produced) == 1 and produced[0] is ev:
                # Pass-through: event unchanged, continue to next finalizer
                continue

            # Transform/replacement: original event is suppressed.
            # Produced events continue from next finalizer index.
            for p in produced:
                work_queue.append((p, i + 1, False))
            transformed = True
            break

        if transformed:
            continue

        if not suppressed:
            # Event reached end of chain without being suppressed or transformed
            # Emit it once
            output.append(ev)

    return output, any_buffered


# ---------------------------------------------------------------------------
# C2D: recovery-capable detection helper
# ---------------------------------------------------------------------------


def _is_recovery_capable(finalizer: StreamFinalizer) -> bool:
    """Check if a finalizer can request recovery.

    A finalizer is recovery-capable if it has a ``decision`` attribute/property
    that can return a ``RecoveryDecision``. This is a narrow and safe check:
    only NudgeContinuationFinalizer, TLSFinalizer, and RLSFinalizer are
    recovery-capable.

    Args:
        finalizer: The finalizer to check.

    Returns:
        True if the finalizer is recovery-capable, False otherwise.
    """
    return hasattr(finalizer, "decision")


# ---------------------------------------------------------------------------
# B2 Recovery: helper functions
# ---------------------------------------------------------------------------


def _check_recovery_decision(
    finalizers_by_priority: List[StreamFinalizer],
    upstream_factory: Optional[callable],
    global_attempt_index: int,
    max_global_recovery_attempts: int,
) -> Optional[RecoveryDecision]:
    """Check if any finalizer has a RecoveryDecision and validate attempt limits.

    Args:
        finalizers_by_priority: Sorted list of finalizers.
        upstream_factory: Factory for creating new upstream streams (required for recovery).
        global_attempt_index: Current global recovery attempt index.
        max_global_recovery_attempts: Maximum allowed global recovery attempts.

    Returns:
        RecoveryDecision if recovery is requested and valid, None otherwise.
    """
    if not upstream_factory:
        # Recovery disabled - no upstream factory provided
        return None

    for fin in finalizers_by_priority:
        if hasattr(fin, "decision") and fin.decision is not None:
            decision = fin.decision
            # Check per-finalizer attempt limit
            if hasattr(fin, "attempt_index") and hasattr(fin, "max_attempts"):
                if fin.attempt_index >= fin.max_attempts:
                    # Per-finalizer limit exceeded - skip this decision
                    continue

            # Check global attempt limit
            if global_attempt_index >= max_global_recovery_attempts:
                # Global limit exceeded - skip this decision
                continue

            # Valid recovery decision
            return decision

    return None


def _restart_upstream_for_recovery(
    finalizers_by_priority: List[StreamFinalizer],
    recovery_decision: RecoveryDecision,
    content_emitted: bool,
    finalizers_buffered: bool,
) -> None:
    """Reset finalizers and prepare for recovery attempt.

    Args:
        finalizers_by_priority: Sorted list of finalizers to reset.
        recovery_decision: The RecoveryDecision that triggered recovery.
        content_emitted: Whether content was emitted in the previous attempt.
        finalizers_buffered: Whether finalizers buffered content in the previous attempt.
    """
    # Reset all finalizers that support reset().  A recovery budget belongs
    # to its originator: observers must become ready for the new upstream
    # attempt, but must not consume their own retry budgets merely because a
    # different finalizer requested a restart.
    preserve_buffer = recovery_decision.merge_strategy == "append_continuation"
    for fin in finalizers_by_priority:
        if hasattr(fin, "reset"):
            if _is_recovery_capable(fin):
                fin.reset(
                    preserve_buffer=preserve_buffer,
                    recovery_attempt=(
                        type(fin).__name__ == recovery_decision.origin_finalizer
                    ),
                )
            else:
                fin.reset(preserve_buffer=preserve_buffer)


def _call_finalize_with_optional_arg(
    finalizer: StreamFinalizer,
    global_attempt_index: int,
) -> List[StreamEvent]:
    """Call finalize() with global_attempt_index if supported.

    Some finalizers (like NudgeContinuationFinalizer) support the
    global_attempt_index parameter, while others (like TimestampFinalizer)
    do not. This helper checks the signature and calls appropriately.

    Args:
        finalizer: The finalizer to call finalize() on.
        global_attempt_index: The global recovery attempt index.

    Returns:
        List of StreamEvents from finalize().
    """
    sig = inspect.signature(finalizer.finalize)
    if "global_attempt_index" in sig.parameters:
        return finalizer.finalize(global_attempt_index)
    else:
        return finalizer.finalize()


def _apply_request_payload_patch(
    current_payload: Optional[dict],
    request_payload_patch: Optional[dict],
) -> dict:
    """Apply ``request_payload_patch`` to the current upstream payload.

    This is the stream runner's mechanism for applying recovery decisions that
    modify the upstream request. It deep-copies the current payload,
    appends the patch messages, and returns the augmented payload without
    mutating the original.

    Patch semantics:
    - Start from the current upstream payload.
    - Deep-copy it before mutation.
    - Append ``patch["messages"]`` to ``payload["messages"]``.
    - Preserve all other payload keys unchanged.
    - Do not mutate the original payload object.

    If no upstream payload exists and a patch with messages is provided:
    - Create ``{"messages": patch["messages"]}`` as a minimal payload.
    - This is safe because the patch messages are the authoritative content
      for the recovery attempt.

    Args:
        current_payload: The current upstream request payload (may be None).
        request_payload_patch: The patch from a RecoveryDecision (may be None).

    Returns:
        The augmented payload dict. If no patch is provided, returns a
        deep copy of the current payload (or a minimal payload if none).
    """
    import copy

    if request_payload_patch is None:
        if current_payload is not None:
            return copy.deepcopy(current_payload)
        return {"messages": []}

    patch_messages = request_payload_patch.get("messages")
    if patch_messages is None:
        if current_payload is not None:
            return copy.deepcopy(current_payload)
        return {}

    if current_payload is None:
        return {"messages": list(patch_messages)}

    augmented = copy.deepcopy(current_payload)
    current_messages = augmented.get("messages", [])
    augmented["messages"] = list(current_messages) + list(patch_messages)
    return augmented


def _collect_finalize_outputs(
    finalizers_by_priority: List[StreamFinalizer],
    serializer: OpenAISSESerializer,
    global_attempt_index: int,
) -> tuple[List[bytes], bool]:
    """Collect serialized finalize() output from all finalizers, with B2 merge.

    B2 merge logic: if NudgeContinuationFinalizer produced merged assistant
    text AND TimestampFinalizer is active, feed Nudge's merged text through
    Timestamp once and emit only Timestamp's output. This avoids emitting
    both Nudge merged text and Timestamp corrected text (duplication source).

    Returns
    -------
    tuple[List[bytes], bool]
        (serialized_bytes, has_tool_call_complete)
    """
    outputs: List[bytes] = []
    has_tcc = False

    # Collect all finalize() outputs (events, not bytes)
    all_events: List[List[StreamEvent]] = []
    for fin in finalizers_by_priority:
        try:
            evts = _call_finalize_with_optional_arg(fin, global_attempt_index)
            all_events.append(evts)
            for fe in evts:
                if isinstance(fe, ToolCallComplete):
                    has_tcc = True
        except RuntimeError:
            all_events.append([])

    # B2 merge: find Nudge and Timestamp finalizers
    nudge_fin = None
    nudge_idx = -1
    ts_fin = None
    for i, fin in enumerate(finalizers_by_priority):
        if type(fin).__name__ == "NudgeContinuationFinalizer":
            nudge_fin = fin
            nudge_idx = i
        if type(fin).__name__ == "TimestampFinalizer":
            ts_fin = fin

    if (
        nudge_fin is not None
        and ts_fin is not None
        and nudge_idx >= 0
        and getattr(nudge_fin, "stream_deltas", False)
    ):
        # Production nudge streams every delta as it arrives.  Timestamp has
        # observed the same text only to decide whether a footer is needed;
        # emitting its full tail here would duplicate already-sent content.
        # Emit its freshly formatted footer alone, then any structural
        # non-text finalizer output (for example a completed tool call).
        if getattr(ts_fin, "_final_delta", None) is not None:
            outputs.append(serializer.serialize_event(
                AssistantTextDelta(delta=ts_fin._format_timestamp())
            ))
        for i, evts in enumerate(all_events):
            if i in (nudge_idx, finalizers_by_priority.index(ts_fin)):
                continue
            for fe in evts:
                if not isinstance(fe, (AssistantTextDelta, ReasoningTextDelta)):
                    outputs.append(serializer.serialize_event(fe))
    elif nudge_fin is not None and ts_fin is not None and nudge_idx >= 0:
        # Extract merged text from Nudge's finalize() output
        nudge_events = all_events[nudge_idx]
        merged_text = ""
        for ev in nudge_events:
            if isinstance(ev, AssistantTextDelta):
                merged_text += ev.delta

        if merged_text:
            # B2+R3 fix: Feed merged text through ToolRewriteFinalizer FIRST,
            # then through TimestampFinalizer. This ensures XML tool call
            # markers in merged text are rewritten before timestamp injection.
            try:
                # Find ToolRewriteFinalizer
                tr_fin = None
                for fin in finalizers_by_priority:
                    if type(fin).__name__ == "ToolRewriteFinalizer":
                        tr_fin = fin
                        break

                # Step 1: Run merged text through ToolRewriteFinalizer
                rewritten_events: List[StreamEvent] = []
                if tr_fin is not None:
                    tr_fin.reset(preserve_buffer=False)
                    rewrite_result = tr_fin.process_event(
                        AssistantTextDelta(delta=merged_text)
                    )
                    # Collect all events from process_event
                    rewritten_events.extend(rewrite_result)
                    # Also collect any events from finalize (e.g. flushed buffers)
                    rewrite_extra = tr_fin.finalize()
                    rewritten_events.extend(rewrite_extra)
                    tr_fin.reset(preserve_buffer=False)
                else:
                    rewritten_events = [AssistantTextDelta(delta=merged_text)]

                # Step 2: Split cleaned text from tool call events
                cleaned_text_parts: List[str] = []
                tool_call_events: List[StreamEvent] = []
                for ev in rewritten_events:
                    if isinstance(ev, AssistantTextDelta):
                        cleaned_text_parts.append(ev.delta)
                    else:
                        tool_call_events.append(ev)
                cleaned_text = "".join(cleaned_text_parts)

                # Step 3: Feed cleaned text through TimestampFinalizer
                ts_fin.reset(preserve_buffer=False)
                ts_prefix_events: List[StreamEvent] = []
                if cleaned_text:
                    ts_prefix_events = ts_fin.process_event(
                        AssistantTextDelta(delta=cleaned_text)
                    )
                ts_result = ts_fin.finalize()

                # Emit tool call events first (before text content)
                for fe in tool_call_events:
                    outputs.append(serializer.serialize_event(fe))

                # A long reconstructed response may overflow Timestamp's
                # tail buffer.  Its safe prefix is part of the response and
                # must precede the final tail/footer.
                for fe in [*ts_prefix_events, *ts_result]:
                    outputs.append(serializer.serialize_event(fe))

                # Emit non-text events from all other finalizers
                for i, evts in enumerate(all_events):
                    if i == nudge_idx:
                        continue  # Skip Nudge's output (merged into chain)
                    # Skip Timestamp's original output (replaced by ts_result)
                    if ts_fin is not None and type(finalizers_by_priority[i]).__name__ == "TimestampFinalizer":
                        continue
                    for fe in evts:
                        if not isinstance(fe, AssistantTextDelta):
                            outputs.append(serializer.serialize_event(fe))
            except RuntimeError:
                # Fallback: emit all collected outputs (preserves pre-merge behavior)
                for evts in all_events:
                    for fe in evts:
                        outputs.append(serializer.serialize_event(fe))
        else:
            # No merged text from Nudge, emit all collected outputs
            for evts in all_events:
                for fe in evts:
                    outputs.append(serializer.serialize_event(fe))
    else:
        # No Nudge+Timestamp pair, emit all collected outputs
        for evts in all_events:
            for fe in evts:
                outputs.append(serializer.serialize_event(fe))

    return outputs, has_tcc


# ---------------------------------------------------------------------------
# C2E: Fallback/Exhaustion Helpers
# ---------------------------------------------------------------------------


def _emit_fallback(
    finalizers_by_priority: List[StreamFinalizer],
    serializer: OpenAISSESerializer,
    fallback_message: str | None = None,
    origin_finalizer: str | None = None,
) -> List[bytes]:
    """Emit a controlled fallback message with Finish and Done.

    This is used when recovery is exhausted (max attempts reached) or when
    upstream_factory raises an exception.

    If TimestampFinalizer is active, the fallback message is passed through
    it to ensure exactly one timestamp footer is appended.

    Args:
        finalizers_by_priority: List of finalizers (may include TimestampFinalizer).
        serializer: The OpenAISSESerializer to use.
        fallback_message: Explicit fallback message.  When omitted, use the
            configured fallback for ``origin_finalizer`` before the generic
            default.
        origin_finalizer: Class name of the finalizer whose recovery was
            exhausted. Its configuration is the only implicit fallback source.

    Returns
    -------
    List[bytes]
        Serialized fallback message, Finish, and Done events.
    """
    events: List[bytes] = []
    if not fallback_message and origin_finalizer:
        for finalizer in finalizers_by_priority:
            if type(finalizer).__name__ != origin_finalizer:
                continue
            candidate = getattr(finalizer, "fallback_message", None)
            if isinstance(candidate, str) and candidate:
                fallback_message = candidate
            break
    if not fallback_message:
        fallback_message = DEFAULT_FALLBACK_MESSAGE

    # Check if TimestampFinalizer is active
    ts_fin = None
    for fin in finalizers_by_priority:
        if type(fin).__name__ == "TimestampFinalizer":
            ts_fin = fin
            break

    if ts_fin is not None:
        # Pass fallback message through TimestampFinalizer
        try:
            ts_fin.reset(preserve_buffer=False)
            ts_fin.process_event(AssistantTextDelta(delta=fallback_message))
            ts_result = ts_fin.finalize()
            for fe in ts_result:
                events.append(serializer.serialize_event(fe))
        except RuntimeError:
            # Fallback: emit raw message if TimestampFinalizer fails
            events.append(serializer.serialize_event(
                AssistantTextDelta(delta=fallback_message)
            ))
    else:
        # No TimestampFinalizer, emit raw message
        events.append(serializer.serialize_event(
            AssistantTextDelta(delta=fallback_message)
        ))

    # Emit Finish(reason="stop")
    events.append(serializer.serialize_event(
        Finish(reason="stop")
    ))

    # Emit Done
    events.append(serializer.serialize_event(
        Done()
    ))

    return events


def _check_recovery_decision_with_fallback(
    finalizers_by_priority: List[StreamFinalizer],
    upstream_factory: Optional[callable],
    global_attempt_index: int,
    max_global_recovery_attempts: int,
) -> tuple[Optional[RecoveryDecision], Optional[str]]:
    """Check if any finalizer has a RecoveryDecision and validate attempt limits.

    C2E extension: also checks if ALL recovery-capable finalizers have
    exceeded their max_attempts, which triggers fallback.

    Fallback is a safety mechanism to prevent infinite loops. It is triggered
    when all recovery-capable finalizers have exceeded their max_attempts
    (per-finalizer limit), AND the global limit has NOT been reached.

    Args:
        finalizers_by_priority: Sorted list of finalizers.
        upstream_factory: Factory for creating new upstream streams (required for recovery).
        global_attempt_index: Current global recovery attempt index.
        max_global_recovery_attempts: Maximum allowed global recovery attempts.

    Returns
    -------
    tuple[Optional[RecoveryDecision], bool]
        (RecoveryDecision if recovery is requested and valid, fallback_origin).
        ``fallback_origin`` is the class name of a finalizer that detected its
        recovery condition after exhausting its own retry budget. ``None``
        means that the current attempt should be accepted normally.
    """
    if not upstream_factory:
        # Recovery disabled - no upstream factory provided
        return None, None

    # If global limit is reached, recovery is rejected (no fallback)
    if global_attempt_index >= max_global_recovery_attempts:
        return None, None

    recovery_decision = None
    for fin in finalizers_by_priority:
        if not hasattr(fin, "decision"):
            # Not a recovery-capable finalizer
            continue

        if fin.decision is not None:
            decision = fin.decision
            # Check per-finalizer attempt limit
            if hasattr(fin, "attempt_index") and hasattr(fin, "max_attempts"):
                if fin.attempt_index >= fin.max_attempts:
                    # Per-finalizer limit exceeded - skip this decision
                    continue

            # Global limit check already done above

            # Valid recovery decision found
            recovery_decision = decision
            break  # First valid decision wins (lowest priority)

    if recovery_decision is not None:
        return recovery_decision, None

    # A fallback is only meaningful when a finalizer actually detected its
    # own bad condition and exhausted its own budget. An unrelated, inactive
    # finalizer must never affect that decision.
    for fin in finalizers_by_priority:
        if getattr(fin, "recovery_exhausted", False):
            return None, type(fin).__name__

    return None, None


# ---------------------------------------------------------------------------
# Internal: unified runner (extracted from _run_async/_run_sync)
# ---------------------------------------------------------------------------


async def _run_core(
    upstream: AsyncIterator[Union[bytes, str]],
    parser: StreamParser,
    finalizers_by_priority: List[StreamFinalizer],
    serializer: OpenAISSESerializer,
    upstream_factory: Optional[callable],
    max_global_recovery_attempts: int,
    global_attempt_index: int,
    payload: Optional[dict] = None,
    execution_usage: Optional[ExecutionUsage] = None,
    dispatcher: Any = None,
    req_id: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """Core runner: parse -> process -> serialize with explicit barriers.

    Accepts an async iterator as input. Callers must ensure sync iterables
    are wrapped via ``_sync_to_async()``.

    B2 Recovery: if upstream_factory is provided and a recovery-capable
    finalizer returns a RecoveryDecision at the Finish barrier, suppress
    the failed attempt Finish/Done, reset finalizers, call upstream_factory
    for a new attempt, and merge the output.

    A live Nudge finalizer observes and forwards non-terminal events
    immediately, deferring only ``Finish`` and ``Done``. Other recovery
    finalizers retain their attempt-isolation guarantee and may still buffer
    output until their own terminal decision.

    O2: When dispatcher is provided, emits RuntimeEvents at recovery
    decision points, retry boundaries, and usage capture.
    """
    done_serialized = False
    finish_serialized = False
    recovery_decision: Optional[RecoveryDecision] = None  # RecoveryDecision or None

    # B2: mutable containers for state across chunks (flat, not nested)
    _partial: List[str] = [""]
    _pending_content: List[str] = []
    _pending_reasoning: List[str] = []
    _pending_tool_calls: Dict[int, Dict[str, Any]] = {}
    _pending_usage: "list[Optional[Dict[str, int]]]" = [None]
    _pending_finish_reason: "list[Optional[str]]" = [None]
    _pending_envelope: Dict[str, Any] = {}
    _has_tool_call_complete: List[bool] = [False]
    _content_emitted: List[bool] = [False]
    _finalizers_buffered: List[bool] = [False]
    _done_emitted_synthetic_finish: List[bool] = [False]

    # Per-attempt buffer used only by recovery finalizers that can reject an
    # attempt's already-produced output (for example RLS/TLS).
    _pass_through_buffer: List[bytes] = []

    _recovery_requires_buffering = (
        upstream_factory is not None
        and any(
            _is_recovery_capable(fin)
            and not (
                type(fin).__name__ == "NudgeContinuationFinalizer"
                and getattr(fin, "stream_deltas", False)
            )
            for fin in finalizers_by_priority
        )
    )

    # B2: loop for recovery attempts
    while True:
        async for raw_chunk in upstream:
            if done_serialized:
                break

            s = raw_chunk if isinstance(raw_chunk, str) else raw_chunk.decode(
                "utf-8", errors="replace"
            )
            _partial[0] += s
            parts = _split_sse_frames(_partial[0])

            for frame in parts[:-1]:
                if done_serialized:
                    break
                if not frame.strip():
                    continue

                parsed = _parse_frame_into_events(
                    frame,
                    _pending_content,
                    _pending_reasoning,
                    _pending_tool_calls,
                    _pending_usage,
                    _pending_finish_reason,
                    _pending_envelope,
                )
                # The parser groups deltas and their Finish barrier in one
                # frame.  Preserve those preceding deltas; only subsequent
                # frames are post-finish and must be dropped.
                _finish_in_this_frame = any(isinstance(item, Finish) for item in parsed)

                # --- finish_reason barrier (Shape B parity fix) ---
                # When finish_reason is recorded by the parser, run finalize()
                # and check for recovery decisions. Finish emission is deferred
                # to post-loop so that usage arriving after finish_reason is
                # captured correctly.
                if _pending_finish_reason[0] is not None and not finish_serialized:
                    # B2 Recovery: collect finalizer output (with merge),
                    # check for recovery decision, then emit if no recovery.
                    _finalizer_output, _tcc_detected = \
                        _collect_finalize_outputs(
                            finalizers_by_priority,
                            serializer,
                            global_attempt_index,
                        )
                    # Track tool_call_complete for finish_reason override
                    _has_tool_call_complete[0] = _tcc_detected

                    # B2 Recovery: check if any finalizer requested recovery
                    # C2E: also check for fallback trigger
                    recovery_decision, _fallback_origin = \
                        _check_recovery_decision_with_fallback(
                            finalizers_by_priority,
                            upstream_factory,
                            global_attempt_index,
                            max_global_recovery_attempts,
                        )

                    if recovery_decision is not None:
                        # O2: emit recovery decision event at finish_reason barrier
                        if _OBSERVABILITY_AVAILABLE and dispatcher is not None:
                            dispatcher.emit(
                                RuntimeEvent(
                                    type="streaming.recovery.decision",
                                    timestamp_ns=time.time_ns(),
                                    source=EventSource(
                                        domain="streaming",
                                        component="recovery",
                                    ),
                                    data={
                                        "kind": recovery_decision.kind,
                                        "origin_finalizer": recovery_decision.origin_finalizer,
                                        "reason": recovery_decision.reason,
                                        "attempt_index": getattr(
                                            recovery_decision, "attempt_index", 0
                                        ),
                                        "max_attempts": getattr(
                                            recovery_decision, "max_attempts", 0
                                        ),
                                        "has_patch": bool(
                                            recovery_decision.request_payload_patch
                                        ),
                                    },
                                    req_id=req_id,
                                    level="BASIC",
                                )
                            )
                        # Suppress finalizer output, Finish/Done, reset finalizers,
                        # restart upstream with new attempt
                        _finalizer_output.clear()
                        _restart_upstream_for_recovery(
                            finalizers_by_priority,
                            recovery_decision,
                            _content_emitted[0],
                            _finalizers_buffered[0],
                        )
                        # This upstream attempt was complete but rejected by a
                        # recovery finalizer.  It still consumed provider work
                        # and must be represented in the usage ledger.
                        if execution_usage is not None:
                            execution_usage.add_attempt(
                                global_attempt_index, _pending_usage[0]
                            )
                        # Increment global attempt index
                        global_attempt_index += 1
                        # Reset state for new attempt (clear mutable container contents)
                        _partial[0] = ""
                        _pending_content.clear()
                        _pending_reasoning.clear()
                        _pending_tool_calls.clear()
                        _pending_usage[0] = None
                        _pending_finish_reason[0] = None
                        _pending_envelope.clear()
                        _has_tool_call_complete[0] = False
                        _content_emitted[0] = False
                        _finalizers_buffered[0] = False
                        _done_emitted_synthetic_finish[0] = False
                        finish_serialized = False
                        # Rejected attempt pass-through output is discarded.
                        _pass_through_buffer.clear()
                        # Restart with new upstream from factory,
                        # applying request_payload_patch if present
                        _augmented_payload = _apply_request_payload_patch(
                            payload,
                            recovery_decision.request_payload_patch,
                        )
                        try:
                            # upstream_factory may be sync or async
                            _new_upstream = upstream_factory(_augmented_payload)
                            if asyncio.iscoroutine(_new_upstream):
                                _new_upstream = await _new_upstream
                            # Wrap sync iterable with _sync_to_async
                            if not hasattr(_new_upstream, "__aiter__"):
                                _new_upstream = _sync_to_async(_new_upstream)
                            upstream = _new_upstream
                        except UPSTREAM_FACTORY_EXCEPTIONS as _e:
                            # C2E: upstream_factory raised - emit fallback
                            _pass_through_buffer.clear()
                            for _frame in _emit_fallback(
                                finalizers_by_priority,
                                serializer,
                                origin_finalizer=recovery_decision.origin_finalizer,
                            ):
                                yield _frame
                            finish_serialized = True
                            done_serialized = True
                            _done_emitted_synthetic_finish[0] = True  # Prevent post-loop Finish
                            break  # Break inner loop, exit outer loop
                        break  # Break inner loop, restart outer while loop

                    # C2E: the finalizer that detected its own exhausted
                    # recovery condition owns the fallback configuration.
                    if _fallback_origin:
                        # Discard rejected attempt pass-through buffer
                        _pass_through_buffer.clear()
                        # Emit fallback message, Finish, Done
                        for _frame in _emit_fallback(
                            finalizers_by_priority,
                            serializer,
                            origin_finalizer=_fallback_origin,
                        ):
                            yield _frame
                        finish_serialized = True
                        done_serialized = True
                        _done_emitted_synthetic_finish[0] = True  # Prevent post-loop Finish
                        break  # Break inner loop, exit outer loop

                    # No recovery: flush pass-through buffer, then emit
                    # buffered finalizer output
                    for _frame in _pass_through_buffer:
                        yield _frame
                    _pass_through_buffer.clear()
                    for _frame in _finalizer_output:
                        yield _frame

                    # Mark finish_serialized so content after finish_reason is dropped.
                    # Finish event itself is emitted post-loop with accumulated usage.
                    finish_serialized = True

                for event in parsed:
                    if done_serialized:
                        break

                    # --- Done: terminal, emit last ---
                    if isinstance(event, Done):
                        # If finish_reason was recorded at barrier but Finish
                        # not yet emitted (deferred for Shape B parity), emit
                        # Finish with accumulated usage before Done.
                        if finish_serialized and not _done_emitted_synthetic_finish[0]:
                            _finish_reason = _pending_finish_reason[0] or "stop"
                            # I9: if ToolCallComplete was emitted, override to tool_calls
                            if _has_tool_call_complete[0]:
                                _finish_reason = "tool_calls"
                            # A provider finish followed by [DONE] is the normal
                            # successful attempt.  Accounting used to be skipped
                            # because the post-loop guard sees the synthetic flag.
                            if execution_usage is not None:
                                execution_usage.add_attempt(
                                    global_attempt_index, _pending_usage[0]
                                )
                                execution_usage.finish_reason = _finish_reason
                            yield serializer.serialize_event(
                                Finish(reason=_finish_reason, usage=_pending_usage[0])
                            )
                            _done_emitted_synthetic_finish[0] = True

                        # If no finish_reason was seen, finalize all finalizers first
                        # so their corrected output appears before Done,
                        # then emit synthetic Finish before Done.
                        if not finish_serialized:
                            _synthetic_reason = "stop"
                            # B2 Recovery: collect finalizer output (with merge),
                            # check for recovery decision, then emit if no recovery.
                            _finalizer_output, _has_tcc = _collect_finalize_outputs(
                                finalizers_by_priority,
                                serializer,
                                global_attempt_index,
                            )
                            if _has_tcc:
                                _synthetic_reason = "tool_calls"

                            # B2 Recovery: check if any finalizer requested recovery
                            # at the Done barrier (no explicit Finish event)
                            # C2E: also check for fallback trigger
                            recovery_decision, _fallback_origin = \
                                _check_recovery_decision_with_fallback(
                                    finalizers_by_priority,
                                    upstream_factory,
                                    global_attempt_index,
                                    max_global_recovery_attempts,
                                )

                            if recovery_decision is not None:
                                # O2: emit recovery decision event
                                if _OBSERVABILITY_AVAILABLE and dispatcher is not None:
                                    dispatcher.emit(
                                        RuntimeEvent(
                                            type="streaming.recovery.decision",
                                            timestamp_ns=time.time_ns(),
                                            source=EventSource(
                                                domain="streaming",
                                                component="recovery",
                                            ),
                                            data={
                                                "kind": recovery_decision.kind,
                                                "origin_finalizer": recovery_decision.origin_finalizer,
                                                "reason": recovery_decision.reason,
                                                "attempt_index": getattr(
                                                    recovery_decision, "attempt_index", 0
                                                ),
                                                "max_attempts": getattr(
                                                    recovery_decision, "max_attempts", 0
                                                ),
                                                "has_patch": bool(
                                                    recovery_decision.request_payload_patch
                                                ),
                                            },
                                            req_id=req_id,
                                            level="BASIC",
                                        )
                                    )
                                # Suppress finalizer output, synthetic Finish/Done,
                                # reset finalizers, restart upstream with new attempt
                                _finalizer_output.clear()
                                _restart_upstream_for_recovery(
                                    finalizers_by_priority,
                                    recovery_decision,
                                    _content_emitted[0],
                                    _finalizers_buffered[0],
                                )
                                # A Done-barrier recovery rejects a completed
                                # upstream attempt just like the Finish barrier.
                                if execution_usage is not None:
                                    execution_usage.add_attempt(
                                        global_attempt_index, _pending_usage[0]
                                    )
                                # Increment global attempt index
                                global_attempt_index += 1
                                # Reset state for new attempt (clear mutable container contents)
                                _partial[0] = ""
                                _pending_content.clear()
                                _pending_reasoning.clear()
                                _pending_tool_calls.clear()
                                _pending_usage[0] = None
                                _pending_finish_reason[0] = None
                                _pending_envelope.clear()
                                _content_emitted[0] = False
                                _finalizers_buffered[0] = False
                                _done_emitted_synthetic_finish[0] = False
                                finish_serialized = False
                                done_serialized = False  # B2 fix: reset done_serialized
                                # Rejected attempt pass-through output is discarded.
                                _pass_through_buffer.clear()
                                # Restart with new upstream from factory,
                                # applying request_payload_patch if present
                                _augmented_payload = _apply_request_payload_patch(
                                    payload,
                                    recovery_decision.request_payload_patch,
                                )
                                try:
                                    # upstream_factory may be sync or async
                                    _new_upstream = upstream_factory(_augmented_payload)
                                    if asyncio.iscoroutine(_new_upstream):
                                        _new_upstream = await _new_upstream
                                    # Wrap sync iterable with _sync_to_async
                                    if not hasattr(_new_upstream, "__aiter__"):
                                        _new_upstream = _sync_to_async(_new_upstream)
                                    upstream = _new_upstream
                                except UPSTREAM_FACTORY_EXCEPTIONS as _e:
                                    # C2E: upstream_factory raised - emit fallback
                                    _pass_through_buffer.clear()
                                    for _frame in _emit_fallback(
                                        finalizers_by_priority,
                                        serializer,
                                        origin_finalizer=recovery_decision.origin_finalizer,
                                    ):
                                        yield _frame
                                    finish_serialized = True
                                    done_serialized = True
                                    _done_emitted_synthetic_finish[0] = True  # Prevent post-loop Finish
                                    break  # Break inner loop, exit outer loop
                                break  # Break inner loop, restart outer while loop

                            # C2E: the finalizer that detected its own
                            # exhausted recovery condition owns the fallback.
                            if _fallback_origin:
                                # Discard rejected attempt pass-through buffer
                                _pass_through_buffer.clear()
                                # Emit fallback message, Finish, Done
                                for _frame in _emit_fallback(
                                    finalizers_by_priority,
                                    serializer,
                                    origin_finalizer=_fallback_origin,
                                ):
                                    yield _frame
                                finish_serialized = True
                                done_serialized = True
                                _done_emitted_synthetic_finish[0] = True  # Prevent post-loop Finish
                                break  # Break inner loop, exit outer loop

                            # No recovery: flush pass-through buffer, then emit
                            # buffered finalizer output
                            for _frame in _pass_through_buffer:
                                yield _frame
                            _pass_through_buffer.clear()
                            for _frame in _finalizer_output:
                                yield _frame

                            # Emit synthetic Finish before Done (no recovery)
                            # Include accumulated usage (Shape B parity).
                            if _content_emitted[0] or _finalizers_buffered[0]:
                                # This is the terminal successful attempt.  Record it
                                # before marking the synthetic finish, otherwise the
                                # post-loop guard skips accounting entirely.
                                if execution_usage is not None:
                                    execution_usage.add_attempt(
                                        global_attempt_index, _pending_usage[0]
                                    )
                                    execution_usage.finish_reason = _synthetic_reason
                                yield serializer.serialize_event(
                                    Finish(reason=_synthetic_reason, usage=_pending_usage[0])
                                )
                                _done_emitted_synthetic_finish[0] = True

                        yield serializer.serialize_event(event)
                        done_serialized = True
                        break

                    # --- After finish_reason recorded: drop content/tool/reasoning ---
                    # Usage chunks are still processed (Shape B parity).
                    if finish_serialized and not _finish_in_this_frame:
                        # Parser no longer emits Finish events; finish_serialized
                        # is set when finish_reason is recorded at the barrier above.
                        # Drop content/reasoning/tool deltas after finish_reason.
                        continue

                    # --- Normal event: pass through finalizers ---
                    # B2: for tool calls, explicitly pass through nudge finalizer
                    # so it can detect has_tool_call. The nudge finalizer has
                    # priority 50 (after tool-call finalizer at 40), so it
                    # won't see ToolCallDelta (which is consumed by tc_finalizer).
                    # We pass it explicitly here.
                    if isinstance(event, (ToolCallDelta, ToolCallComplete)):
                        for fin in finalizers_by_priority:
                            if type(fin).__name__ == "NudgeContinuationFinalizer":
                                fin.process_event(event)

                    events, buffered = _pass_through_finalizers_with_buffer_tracking(
                        [event],
                        finalizers_by_priority,
                    )
                    if buffered:
                        _finalizers_buffered[0] = True

                    for ev in events:
                        _serialized = serializer.serialize_event(ev)
                        if _recovery_requires_buffering:
                            _pass_through_buffer.append(_serialized)
                        else:
                            yield _serialized
                        _content_emitted[0] = True

            if recovery_decision is not None:
                # Restart outer loop with new upstream
                break

            _partial[0] = parts[-1] if parts else ""

            # Flush accumulated content (parser doesn't flush if no finish_reason).
            # If Finish was already serialized, skip - content after Finish is dropped.
            if not finish_serialized and _pending_content:
                content = "".join(_pending_content)
                events, buffered = _pass_through_finalizers_with_buffer_tracking(
                    [AssistantTextDelta(delta=content)],
                    finalizers_by_priority,
                )
                if buffered:
                    _finalizers_buffered[0] = True
                for ev in events:
                    _serialized = serializer.serialize_event(ev)
                    if _recovery_requires_buffering:
                        _pass_through_buffer.append(_serialized)
                    else:
                        yield _serialized
                    _content_emitted[0] = True
                _pending_content.clear()
            if not finish_serialized and _pending_reasoning:
                reasoning = "".join(_pending_reasoning)
                # Pass accumulated reasoning through finalizers first
                events, buffered = _pass_through_finalizers_with_buffer_tracking(
                    [ReasoningTextDelta(delta=reasoning)],
                    finalizers_by_priority,
                )
                if buffered:
                    _finalizers_buffered[0] = True
                for ev in events:
                    _serialized = serializer.serialize_event(ev)
                    if _recovery_requires_buffering:
                        _pass_through_buffer.append(_serialized)
                    else:
                        yield _serialized
                    _content_emitted[0] = True
                _pending_reasoning.clear()

        # Check if we broke out for recovery
        if recovery_decision is not None:
            # Reset recovery_decision for next attempt
            recovery_decision = None
            continue

        # Normal exit from upstream loop
        break

    # Post-loop: emit Finish with accumulated usage (Shape B parity fix).
    # finish_serialized is True when finish_reason was recorded at the barrier.
    # It is False only when no finish_reason was seen (stream exhaustion).
    if not done_serialized or not _done_emitted_synthetic_finish[0]:
        # Capture usage from pending_usage[0] at stream exhaustion
        # (Rule 5: read BEFORE any potential reset — here we are at exit)
        # Always record the attempt; raw_usage may be None if provider
        # did not report usage metadata. add_attempt() handles None
        # correctly: increments upstream_attempts but not usage_reported_attempts.
        if execution_usage is not None:
            execution_usage.add_attempt(
                global_attempt_index,
                _pending_usage[0],
            )

        # If Done was serialized without finish_reason, finalize finalizers now.
        if done_serialized and not _done_emitted_synthetic_finish[0]:
            _synthetic_reason = "stop"
            _post_outputs, _post_tcc = _collect_finalize_outputs(
                finalizers_by_priority,
                serializer,
                global_attempt_index,
            )
            if _post_tcc:
                _synthetic_reason = "tool_calls"
            for _frame in _post_outputs:
                yield _frame

        # Determine finish reason and emit Finish with accumulated usage.
        # - If finish_reason was recorded at barrier: use that reason.
        # - If no finish_reason seen (stream exhaustion): use synthetic reason.
        if finish_serialized:
            # finish_reason was recorded; use it (may have been overridden by tool_calls)
            _finish_reason = _pending_finish_reason[0] or "stop"
        else:
            # No finish_reason seen; emit synthetic Finish
            _finish_reason = "stop"
            if not done_serialized:
                _post_outputs, _post_tcc = _collect_finalize_outputs(
                    finalizers_by_priority,
                    serializer,
                    global_attempt_index,
                )
                if _post_tcc:
                    _finish_reason = "tool_calls"
                for _frame in _post_outputs:
                    yield _frame

        # Emit Finish with accumulated usage (Shape B parity: usage after finish_reason captured)
        if not _done_emitted_synthetic_finish[0] and (
            _content_emitted[0] or _finalizers_buffered[0]
        ):
            # Propagate finish_reason to ExecutionUsage for metrics emission.
            if execution_usage is not None:
                execution_usage.finish_reason = _finish_reason
            yield serializer.serialize_event(
                Finish(reason=_finish_reason, usage=_pending_usage[0])
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_stream(
    upstream_chunks: Union[AsyncIterator, Iterable],
    finalizers: Optional[List[StreamFinalizer]] = None,
    serializer: Optional[OpenAISSESerializer] = None,
    parser: Optional[StreamParser] = None,
    upstream_factory: Optional[callable] = None,
    max_global_recovery_attempts: int = 10,
    payload: Optional[dict] = None,
    execution_usage: Optional[ExecutionUsage] = None,
    dispatcher: Any = None,
    req_id: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """Run the canonical streaming pipeline with explicit barrier semantics.

    Flow:

    1. Parse each upstream SSE chunk -> ``StreamEvent``
    2. Pass each event through finalizers in priority order
    3. On ``Finish`` barrier: run ``finalize()`` on all finalizers,
       serialize finalizer output, then serialize ``Finish``
    4. On ``Done``: serialize ``Done``, stop processing
    5. Post-loop: if no Finish was seen, run finalize() on all finalizers
    6. B2 Recovery: if a recovery-capable finalizer returns a RecoveryDecision,
       suppress the failed attempt Finish/Done, reset finalizers, call
       upstream_factory for a new attempt, and merge the output.

    Parameters
    ----------
    upstream_chunks:
        Async iterator or sync iterable yielding raw SSE bytes/strings from
        the upstream.  The runner accepts both for flexibility in testing.
    finalizers:
        Optional list of ``StreamFinalizer`` instances.  Ordered by
        ``priority`` (lower runs first).  Default is empty (pass-through).
    execution_usage:
        Optional ``ExecutionUsage`` object for Phase 1 internal accounting.
        Caller creates the object and passes it in so the same reference
        is available after the generator completes.  If ``None``, a new
        empty ``ExecutionUsage`` is created internally (but not exposed).
    serializer:
        Optional ``OpenAISSESerializer`` instance.  Default creates a new one.
    parser:
        Optional ``StreamParser`` instance.  Default creates a new one.
    upstream_factory:
        Optional callable that returns a new async iterator for recovery
        attempts. Signature: ``upstream_factory(payload: dict) -> AsyncIterator[bytes]``.
        The ``payload`` parameter carries the current upstream request
        payload so that recovery decisions can mutate the upstream request
        (e.g. by appending continuation messages).
        If None, recovery is disabled and B1 behavior is used.
    max_global_recovery_attempts:
        Maximum global recovery attempts across all finalizers. Default 10.
    payload:
        Optional upstream request payload dict (e.g. ``{"messages": [...]}``).
        Passed through to ``upstream_factory`` so that recovery decisions
        that carry a ``request_payload_patch`` can augment the payload
        before retrying.

    Yields
    ------
    bytes:
        SSE frames ready to send to the downstream client.

    Barrier semantics:

    - **Normal content events**: pass through finalizers -> serialize immediately
    - **Finish**: barrier -> run ``finalize()`` on all finalizers -> serialize
      finalizer output -> serialize ``Finish`` -> no more ``AssistantTextDelta``
      after this point
    - **Done**: serialize ``Done`` -> break (terminal event, last in output)
    - **Post-loop**: if no Finish was seen, finalize all finalizers.
      If content was buffered by finalizers but not flushed, emit a synthetic
      ``Finish`` after the finalizer output.
    - **B2 Recovery**: if a recovery-capable finalizer returns a RecoveryDecision
      at the Finish barrier, suppress the failed attempt Finish/Done, reset
      finalizers (preserving buffers for append_continuation), call
      upstream_factory for a new attempt, and merge the output.

    Policy for content after Finish:
    - Content/reasoning/tool deltas after ``Finish`` are **dropped**.
      The per-chunk flush of accumulated pending content is guarded by
      ``if not finish_serialized`` so nothing is emitted after ``Finish``.
    - A per-event ``RuntimeError`` is raised as a safety check if a
      non-Done event appears after ``Finish`` in the same parsed frame.
      This guarantees I4 (no assistant text after Finish).

    Policy for invalid JSON:
    - Invalid JSON frames are silently skipped by the parser.
    - The runner never crashes on invalid JSON.

    Policy for Done:
    - ``Done`` is the final event serialized by the runner.
    - No content is emitted after ``Done``.

    B2 Recovery Policy:
    - If ``upstream_factory`` is None, recovery is disabled (B1 behavior).
    - If a recovery-capable finalizer returns a RecoveryDecision:
      - Check attempt limits (per-finalizer and global).
      - If valid: apply ``request_payload_patch`` (if present) to the
        upstream payload, suppress Finish/Done, reset finalizers, restart
        upstream with the augmented payload.
      - If invalid: serialize the failed attempt output as-is.
    - For ``append_continuation`` strategy: preserve buffers across attempts.
    - For ``replace`` strategy: clear buffers on recovery.
    - Exactly one final Finish and Done are serialized downstream.
    """
    if finalizers is None:
        finalizers = []
    if serializer is None:
        serializer = OpenAISSESerializer()
    if parser is None:
        parser = StreamParser(dispatcher=dispatcher)
    else:
        # Ensure parser has dispatcher if one was provided
        if dispatcher is not None and getattr(parser, "_dispatcher", None) is None:
            parser._dispatcher = dispatcher

    finalizers_by_priority = sorted(finalizers, key=lambda f: f.priority)

    # B2 Recovery: global attempt counter
    _global_attempt_index = 0

    # ExecutionUsage accounting object (Phase 1 internal accounting)
    # Caller passes in the object so the same reference is available after
    # the generator completes.  If None, create a new empty one internally.
    if execution_usage is None:
        _execution_usage = ExecutionUsage.empty()
    else:
        _execution_usage = execution_usage

    # Check if it's an async iterator (has __aiter__ method)
    is_async = hasattr(upstream_chunks, "__aiter__") and callable(
        getattr(upstream_chunks, "__aiter__")
    )

    if is_async:
        async for chunk in _run_core(
            upstream_chunks,
            parser,
            finalizers_by_priority,
            serializer,
            upstream_factory,
            max_global_recovery_attempts,
            _global_attempt_index,
            payload,
            _execution_usage,
            dispatcher,
            req_id,
        ):
            yield chunk
    else:
        # Wrap sync iterable with _sync_to_async for unified _run_core
        async for chunk in _run_core(
            _sync_to_async(upstream_chunks),
            parser,
            finalizers_by_priority,
            serializer,
            upstream_factory,
            max_global_recovery_attempts,
            _global_attempt_index,
            payload,
            _execution_usage,
            dispatcher,
            req_id,
        ):
            yield chunk


# ---------------------------------------------------------------------------
# Convenience: collect events for testing
# ---------------------------------------------------------------------------


def collect_stream_events(
    chunks: Iterable[Union[bytes, str]],
    finalizers: Optional[List[StreamFinalizer]] = None,
) -> List[StreamEvent]:
    """Collect all events from upstream chunks (sync convenience).

    Useful for unit tests that want to inspect the event pipeline
    without going through serialization.

    Respects the same Finish barrier as ``run_stream``:
    when ``Finish`` is seen, finalizers are finalized first, then
    ``Finish`` is appended.  Any non-Done events after ``Finish``
    are dropped.

    Parameters
    ----------
    chunks:
        Raw SSE bytes/strings.
    finalizers:
        Finalizers to apply (ordered by priority).

    Returns
    -------
    list[StreamEvent]
        All events after finalizer processing.
    """
    if finalizers is None:
        finalizers = []

    finalizers_by_priority = sorted(finalizers, key=lambda f: f.priority)
    _partial: str = ""
    _pending_content: List[str] = []
    _pending_reasoning: List[str] = []
    _pending_tool_calls: Dict[int, Dict[str, Any]] = {}
    _pending_usage: "list[Optional[Dict[str, int]]]" = [None]
    _pending_finish_reason: "list[Optional[str]]" = [None]
    _pending_envelope: Dict[str, Any] = {}

    pending: List[StreamEvent] = []
    finish_serialized = False

    for raw_chunk in chunks:
        s = raw_chunk if isinstance(raw_chunk, str) else raw_chunk.decode(
            "utf-8", errors="replace"
        )
        _partial += s
        parts = _split_sse_frames(_partial)
        for frame in parts[:-1]:
            if not frame.strip():
                continue
            parsed_events = _parse_frame_into_events(
                frame,
                _pending_content,
                _pending_reasoning,
                _pending_tool_calls,
                _pending_usage,
                _pending_finish_reason,
                _pending_envelope,
            )

            # Process events from this frame (including flushed content from
            # finish_reason barrier) before marking finish_serialized.
            for event in parsed_events:
                # --- Done: terminal ---
                if isinstance(event, Done):
                    pending.append(event)
                    continue

                # --- After finish_reason recorded: drop non-Done events ---
                if finish_serialized:
                    continue

                # Pass through finalizers
                events, _ = _pass_through_finalizers_with_buffer_tracking(
                    [event],
                    finalizers_by_priority,
                )
                pending.extend(events)

            # --- finish_reason barrier (Shape B parity fix) ---
            # After processing events, run finalize() on all finalizers.
            if _pending_finish_reason[0] is not None and not finish_serialized:
                for fin in finalizers_by_priority:
                    try:
                        for fe in fin.finalize():
                            pending.append(fe)
                    except RuntimeError:
                        pass
                finish_serialized = True

        _partial = parts[-1] if parts else ""

    # Post-loop: flush any remaining pending content, then finalize/emit Finish
    if not finish_serialized:
        # Flush accumulated content (same as runner's per-chunk flush)
        if _pending_content:
            content = "".join(_pending_content)
            events, _ = _pass_through_finalizers_with_buffer_tracking(
                [AssistantTextDelta(delta=content)],
                finalizers_by_priority,
            )
            pending.extend(events)
            _pending_content.clear()
        if _pending_reasoning:
            reasoning = "".join(_pending_reasoning)
            # Pass accumulated reasoning through finalizers (same pattern as
            # _run_async/_run_sync) so finalizers like RLSFinalizer can observe
            # accumulated reasoning before finalize() is called.
            events, _ = _pass_through_finalizers_with_buffer_tracking(
                [ReasoningTextDelta(delta=reasoning)],
                finalizers_by_priority,
            )
            pending.extend(events)
            _pending_reasoning.clear()

        # Finalize all finalizers
        _has_tool_call_complete = any(
            isinstance(e, ToolCallComplete) for e in pending
        )
        for fin in finalizers_by_priority:
            try:
                for fe in fin.finalize():
                    pending.append(fe)
                    if isinstance(fe, ToolCallComplete):
                        _has_tool_call_complete = True
            except RuntimeError:
                pass

    # Emit Finish with accumulated usage (Shape B parity fix).
    # - If finish_reason was recorded at barrier: use that reason.
    # - If no finish_reason seen (stream exhaustion): use synthetic reason.
    if not any(isinstance(e, Finish) for e in pending):
        if _pending_finish_reason[0] is not None:
            _finish_reason = _pending_finish_reason[0]
        else:
            _has_tool_call_complete = any(
                isinstance(e, ToolCallComplete) for e in pending
            )
            _finish_reason = "tool_calls" if _has_tool_call_complete else "stop"
        pending.append(Finish(reason=_finish_reason, usage=_pending_usage[0]))

    return pending
