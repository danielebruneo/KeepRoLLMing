"""NudgeContinuationFinalizer — lazy-output detection for canonical streaming pipeline.

Detects when an assistant response ends with a lazy pattern (e.g. ``:$``),
buffers the accumulated assistant text, and at ``finalize()`` produces a
``RecoveryDecision(kind="append_continuation")`` describing how the runner
should request a continuation from the upstream model.

**Key semantic rule:** ModelNudge lazy-response handling is NOT destructive
retry. The original assistant output is preserved and the continuation is
appended — never discarded.

This module is part of the streaming finalizer pipeline and the stream runner — it only
exposes a ``process_event``/``finalize`` contract and a ``RecoveryDecision``
dataclass for unit testing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Done,
    Finish,
    Keepalive,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)
from keeprollming.streaming.finalizers import StreamFinalizer


# ---------------------------------------------------------------------------
# RecoveryDecision — control signal from finalizer → runner
# ---------------------------------------------------------------------------


@dataclass
class RecoveryDecision:
    """Request to recover the upstream stream with modified state.

    This is a **control signal** from finalizer to runner — not an event
    emitted into the downstream SSE.

    Attributes
    ----------
    kind:
        One of ``"replace"``, ``"append_continuation"``, ``"intervention"``.
    reason:
        Human-readable explanation.
    priority:
        Numeric priority of the requesting finalizer (lower = higher
        priority in arbitration).
    origin_finalizer:
        Name of the finalizer that requested recovery.
    attempt_index:
        Per-finalizer attempt counter (0-based).
    max_attempts:
        Maximum recovery attempts for this finalizer.
    global_attempt_index:
        Global recovery attempt counter across all finalizers.
    request_payload_patch:
        Optional dict describing how to modify the upstream request
        (messages, system prompt, etc.) for the recovery attempt.
    preserve_output_so_far:
        If ``True``, the original attempt's buffered output is preserved
        and merged with recovery output.
    merge_strategy:
        One of ``"replace"``, ``"append_continuation"``,
        ``"inject_tool_result"``, ``"intervention_specific"``.
    diagnostics:
        Optional dict for debugging — detected pattern, tool signatures,
        conversation turn indices, etc.
    """

    kind: str
    reason: str
    priority: int
    origin_finalizer: str
    attempt_index: int
    max_attempts: int
    global_attempt_index: int
    request_payload_patch: Optional[Dict[str, Any]] = None
    preserve_output_so_far: bool = True
    merge_strategy: str = "replace"
    diagnostics: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# NudgeContinuationFinalizer
# ---------------------------------------------------------------------------


class NudgeContinuationFinalizer(StreamFinalizer):
    """Detect lazy output and produce a continuation decision.

    Implements the **continuation / append** recovery strategy for ModelNudge.

    Priority: 50 — runs after timestamp (20) and tool-call (40) finalizers.

    **B1 lifecycle:** ``finalize()`` is single-use per instance — second call
    raises ``RuntimeError``. This matches the current ``StreamFinalizer``
    contract (``TimestampFinalizer``, ``ToolCallFinalizer``).

    **B2 note:** During runner recovery integration the same instance may be
    reused across upstream attempts (the text buffer is preserved). B2 will
    introduce per-attempt lifecycle / reset semantics so ``finalize()`` can be
    called once per attempt without raising on the second call. This does not
    change B1 behavior.

    Parameters
    ----------
    trigger_patterns:
        List of compiled regex patterns that match the end of a lazy response.
        Default ``[":$"]`` (colon followed by end-of-string).
    nudge_message:
        Message to inject for the continuation request.
        Default ``"Continue."``
    max_attempts:
        Maximum nudge attempts before giving up. Default ``3``.
    tail_buffer_size:
        Maximum characters to retain in the rolling tail buffer.
        Default ``1024``.

    Return semantics (process_event)
    --------------------------------
    * ``AssistantTextDelta`` / ``ReasoningTextDelta`` → record the delta for
      terminal lazy detection.  In production they are also forwarded live;
      only ``Finish`` and ``Done`` wait for the recovery decision.
    * ``ToolCallDelta`` / ``ToolCallComplete`` → returns ``[event]`` (pass-
      through — tool calls prevent nudge continuation).
    * ``Finish`` → returns ``[event]`` (pass-through — triggers finalize in
      runner).
    * ``Done`` / ``Keepalive`` → returns ``[event]`` (pass-through).
    """

    priority: int = 50

    def __init__(
        self,
        trigger_patterns: Optional[List[str]] = None,
        nudge_message: str = "Continue.",
        max_attempts: int = 3,
        tail_buffer_size: int = 1024,
        stream_deltas: bool = False,
    ) -> None:
        super().__init__()
        self.nudge_message = nudge_message
        self.max_attempts = max_attempts
        self.tail_buffer_size = tail_buffer_size
        # The finalizer factory enables this for live proxy traffic.  Keeping the
        # default preserves the standalone finalizer contract used by callers
        # that intentionally want finalize()-owned output.
        self.stream_deltas = stream_deltas
        self._trigger_patterns: List[re.Pattern[str]] = []

        # Compile trigger patterns
        for pattern in trigger_patterns or [":$"]:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._trigger_patterns.append(compiled)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

        # Buffers
        self._text_buffer = ""
        self._reasoning_buffer = ""
        self._has_tool_call = False
        self._lazy_detected = False
        self._finalized = False
        self._decision: Optional[RecoveryDecision] = None
        # Pass-through events captured during process_event (re-emitted in finalize)
        self._passthrough_events: List[StreamEvent] = []
        # B2: attempt counter for recovery
        self._attempt_index = 0
        # B2: separator injection for established continuation behavior. When True, the next
        # AssistantTextDelta will be prefixed with "\\n" to match established
        # `accumulator += "\\n" + retry_content` rule. Reset to True on
        # each recovery restart (preserve_buffer=True). Cleared on first
        # AssistantTextDelta of the continuation attempt.
        self._pending_text_separator = False

    # ── StreamFinalizer contract ──────────────────────────────────

    def reset(
        self,
        preserve_buffer: bool = True,
        recovery_attempt: bool = True,
    ) -> None:
        """Reset finalizer state for a new recovery attempt.

        B2 recovery integration: when the runner restarts the upstream
        stream after a recovery decision, this method is called to reset
        the finalizer for the new attempt.

        Args:
            preserve_buffer: If True (default for append_continuation),
                preserve _text_buffer and _reasoning_buffer for continuation.
                Also sets _pending_text_separator=True so the next
                AssistantTextDelta gets a "\\n" prefix (established continuation behavior).
                If False (for replace strategy), clear all buffers and
                separator state.
            recovery_attempt: Whether this finalizer requested the recovery
                attempt. Only the originating finalizer consumes one of its
                per-finalizer recovery attempts.
        """
        self._finalized = False
        self._decision = None
        self._passthrough_events.clear()
        self._lazy_detected = False
        if recovery_attempt:
            self._attempt_index += 1
        if preserve_buffer:
            # Next AssistantTextDelta will be prefixed with "\\n" to match
            # established `accumulator += "\\n" + retry_content` rule.
            self._pending_text_separator = True
        else:
            self._text_buffer = ""
            self._reasoning_buffer = ""
            self._has_tool_call = False
            self._pending_text_separator = False

    def process_event(self, event: StreamEvent) -> list[StreamEvent]:
        """Process a single StreamEvent from the pipeline.

        * text/reasoning deltas are always recorded; live mode forwards them
          immediately while retaining only the accumulated semantic state.
        * ``ToolCallDelta`` / ``ToolCallComplete`` → pass-through (tool calls
          prevent nudge continuation)
        * ``Finish`` → pass-through (finalize is called by runner on barrier)
        * ``Done`` / ``Keepalive`` → pass-through
        """
        # B2: allow process_event after reset (for recovery attempts)
        # Only raise if finalize() was called AND reset() was not called after.
        if self._finalized:
            raise RuntimeError(
                "NudgeContinuationFinalizer.finalize() already called without reset()"
            )

        if isinstance(event, AssistantTextDelta):
            # ``reset(preserve_buffer=True)`` marks the first continuation
            # delta.  The original lazy text was already delivered, so emit
            # the canonical separator as its own downstream delta before the
            # upstream continuation.  This preserves live streaming without
            # rewriting either upstream fragment.
            emit_separator = self.stream_deltas and self._pending_text_separator
            self._buffer_text(event.delta)
            if self.stream_deltas:
                if emit_separator:
                    return [AssistantTextDelta(delta="\n"), event]
                return [event]
            return []

        if isinstance(event, ReasoningTextDelta):
            self._buffer_reasoning(event.delta)
            return [event] if self.stream_deltas else []

        if isinstance(event, (ToolCallDelta, ToolCallComplete)):
            self._has_tool_call = True
            self._passthrough_events.append(event)
            return [event]

        # Pass through Finish, Done, Keepalive, Error, etc.
        # Capture for re-emission in finalize().
        self._passthrough_events.append(event)
        return [event]

    def finalize(self, global_attempt_index: int = 0) -> list[StreamEvent]:
        """Finalize before ``Finish`` is emitted.

        Detects lazy patterns in accumulated text. If lazy:
        * Produces a ``RecoveryDecision(kind="append_continuation")``
        * Returns buffered text + reasoning as ``AssistantTextDelta`` +
          ``ReasoningTextDelta`` events.

        If not lazy:
        * ``_decision`` is ``None``
        * Returns buffered text + reasoning as ``AssistantTextDelta`` +
          ``ReasoningTextDelta`` events.

        Must be idempotent-safe: second call raises ``RuntimeError``.

        **B2 recovery integration:** This method is called once per upstream
        attempt. The ``_finalized`` flag is reset by ``reset()`` before the
        next attempt. The ``global_attempt_index`` parameter is passed by the
        runner to track recovery attempts across all finalizers.

        Args:
            global_attempt_index: Global recovery attempt counter passed by
                the runner. Used in RecoveryDecision for arbitration.

        Returns
        -------
        list[StreamEvent]
            Flushed assistant text and reasoning as individual events.
            Passthrough events (Finish, Done, etc.) are NOT included —
            they are handled separately by the runner's pipeline.
        """
        if self._finalized:
            raise RuntimeError(
                "NudgeContinuationFinalizer.finalize() already called without reset()"
            )
        self._finalized = True

        # Determine if lazy (only if no tool calls — tool calls prevent nudge)
        self._lazy_detected = (
            not self._has_tool_call
            and self._matches_lazy_response(self._text_buffer)
        )

        # Build recovery decision if lazy
        if self._lazy_detected:
            self._decision = RecoveryDecision(
                kind="append_continuation",
                reason=(
                    "Lazy output detected — model stopped at trigger pattern. "
                    "Requesting continuation."
                ),
                priority=self.priority,
                origin_finalizer="NudgeContinuationFinalizer",
                attempt_index=self._attempt_index,
                max_attempts=self.max_attempts,
                global_attempt_index=global_attempt_index,
                request_payload_patch={
                    "messages": [
                        {"role": "assistant", "content": self._text_buffer},
                        {"role": "user", "content": self.nudge_message},
                    ],
                },
                preserve_output_so_far=True,
                merge_strategy="append_continuation",
                diagnostics={
                    "detected_pattern": self._text_buffer.rstrip(),
                    "lazy_prefix": self._text_buffer,
                },
            )

        # In live mode the client has already received every non-terminal
        # delta.  Re-emitting here would duplicate the transcript.  Legacy
        # standalone mode retains finalize-owned output for compatibility with
        # the finalizer's direct unit contract.
        if self.stream_deltas:
            return []

        # Flush buffers as events (reasoning before text, KRM canonical channel order)
        # See _docs/streaming-v2-reasoning-order-contract.md (Option A — Grouped).
        events: list[StreamEvent] = []
        if self._reasoning_buffer:
            events.append(ReasoningTextDelta(delta=self._reasoning_buffer))
        if self._text_buffer:
            events.append(AssistantTextDelta(delta=self._text_buffer))
        return events

    def get_passthrough_events(self) -> list[StreamEvent]:
        """Return captured passthrough events (Finish, Done, etc.).

        These events are returned from ``process_event()`` as-is but are
        also captured for the runner to re-emit after ``finalize()``
        produces the text/reasoning flush.
        """
        return list(self._passthrough_events)

    # ── Public inspection helpers (for tests) ─────────────────────

    @property
    def decision(self) -> Optional[RecoveryDecision]:
        """The RecoveryDecision produced by finalize(), or None."""
        return self._decision

    @property
    def lazy_detected(self) -> bool:
        """Whether a lazy pattern was detected during finalize()."""
        return self._lazy_detected

    @property
    def accepted_prefix(self) -> str:
        """The buffered assistant text (original output)."""
        return self._text_buffer

    @property
    def has_tool_call(self) -> bool:
        """Whether any tool call event was seen during processing."""
        return self._has_tool_call

    @property
    def attempt_index(self) -> int:
        """Per-finalizer attempt counter for recovery."""
        return self._attempt_index

    # ── Internal helpers ──────────────────────────────────────────

    def _buffer_text(self, delta: str) -> None:
        """Append delta to the text tail buffer.

        established continuation behavior: when _pending_text_separator is True (i.e., this is the
        first AssistantTextDelta of a recovery continuation attempt),
        prepend "\\n" to match established `accumulator += "\\n" + retry_content`.
        The separator is consumed exactly once on the first delta, regardless
        of whether _text_buffer is empty or not.
        """
        if self._pending_text_separator:
            self._text_buffer += "\n"
            self._pending_text_separator = False
        self._text_buffer += delta

    def _buffer_reasoning(self, delta: str) -> None:
        """Append delta to the reasoning tail buffer."""
        self._reasoning_buffer += delta

    def _matches_lazy_response(self, content: str) -> bool:
        """Check if accumulated content matches any lazy trigger pattern.

        Patterns ending with ``$`` match at END of string (suffix matching).
        Other patterns can match anywhere in the text.
        Uses ``\\Z`` for true end-of-string anchor to handle Unicode/emoji
        correctly.
        """
        if not self._trigger_patterns:
            return False

        for pattern in self._trigger_patterns:
            # Handle $ anchor specially to support Unicode/emoji correctly
            if pattern.pattern.endswith("$"):
                # Remove trailing $ and check manually
                base_pattern = pattern.pattern[:-1]

                # Special case: just ":", do simple string check (case insensitive)
                if base_pattern == ":":
                    stripped = content.rstrip()
                    if len(stripped) > 0 and stripped[-1].lower() == ":":
                        return True
                else:
                    # Other patterns ending with $ - use regex with \Z anchor
                    regex_with_z = base_pattern + r"\Z"
                    match = re.search(regex_with_z, content, pattern.flags)
                    if match:
                        return True
            else:
                # Regular pattern (no $ anchor) - can match anywhere
                match = pattern.search(content)
                if match:
                    return True

        return False
