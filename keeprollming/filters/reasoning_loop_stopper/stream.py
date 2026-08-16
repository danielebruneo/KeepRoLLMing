"""RLSFinalizer — Reasoning-Loop Stopper for canonical streaming pipeline.

Detects reasoning-loop patterns in streaming output and produces a
``RecoveryDecision`` with ``kind="intervention"`` and
``merge_strategy="intervention_specific"`` so the runner can inject a
user reasoning nudge message and break the loop.

**Observer semantics:** RLSFinalizer copies ReasoningTextDelta events into
its internal buffer for detection but **returns the original event** (not
``[]``). It does NOT consume reasoning events.

This module is **independent** of the previous pipeline and can be unit-tested
in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from keeprollming.streaming.events import (
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
from keeprollming.streaming.finalizers import StreamFinalizer
from keeprollming.filters.nudge.stream import RecoveryDecision


class RLSFinalizer(StreamFinalizer):
    """Detect reasoning-loop patterns and produce intervention recovery decisions.

    **Observer semantics:**
    - ReasoningTextDelta events are copied into the RLS buffer for
      detection purposes.
    - The original event is **always returned** (``[event]``), never
      consumed.
    - Does NOT consume reasoning events — no-loop reasoning must not be
      dropped.

    **Loop detection:**
    - Identical reasoning text compared with the last reasoning from
      conversation history.
    - Tool-call disambiguation: same reasoning + different tool calls =
      not a loop.
    - (Experimental, opt-in) Within-stream repeated-suffix detection:
      detects when the model repeats the same reasoning multiple times
      in a single response. Disabled by default due to potential false
      positives with tool-call disambiguation.

    **On loop detection:**
    - Creates a ``RecoveryDecision(kind="intervention")`` with
      ``merge_strategy="intervention_specific"``.
    - ``request_payload_patch`` contains a user reasoning nudge message.

    **On no loop:**
    - ``decision`` is ``None``.
    - No output mutation — original events pass through unchanged.

    **Max attempts:**
    - If ``attempt_index >= max_attempts``, the finalizer does NOT produce
      a decision. The fallback/recovery exhaustion is handled by C2 runner
      integration.

    A loop is always determined from the current request's conversation
    history.  It deliberately never uses process-global prior-request state:
    two unrelated users producing similar reasoning must remain isolated.

    Priority: 60 — runs after TLSFinalizer (55).

    Parameters
    ----------
    max_attempts:
        Maximum recovery attempts before giving up. Default ``5``.
    nudge_message:
        User message to nudge the model out of the reasoning loop.
        Default ``"Your reasoning is repeating. Think differently or provide a direct answer."``
    detect_within_stream_loop:
        If True, enable experimental within-stream repeated-suffix
        detection. Disabled by default. Default ``False``.
    """

    priority: int = 60

    def __init__(
        self,
        max_attempts: int = 5,
        nudge_message: str = (
            "Your reasoning is repeating. Think differently or provide "
            "a direct answer."
        ),
        detect_within_stream_loop: bool = False,
        conversation_reasoning: Optional[str] = None,
        conversation_tool_calls: Sequence[Mapping[str, Any]] | None = None,
        fallback_message: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.max_attempts = max_attempts
        self.nudge_message = nudge_message
        self.detect_within_stream_loop = detect_within_stream_loop
        self.fallback_message = fallback_message

        # Local state
        self._reasoning_buffer: str = ""
        self._reasoning_deltas: List[str] = []
        self._conversation_reasoning = conversation_reasoning
        self._last_reasoning_from_conversation: Optional[str] = conversation_reasoning
        self._conversation_tool_signatures = self._signatures_from_history(
            conversation_tool_calls or ()
        )
        self._tool_call_signatures: List[str] = []
        self._has_tool_call = False
        self._decision: Optional[RecoveryDecision] = None
        self._finalized = False
        self._attempt_index = 0

    # ── StreamFinalizer contract ──────────────────────────────────

    def reset(
        self,
        preserve_buffer: bool = True,
        recovery_attempt: bool = True,
    ) -> None:
        """Reset finalizer state for a new recovery attempt.

        Clears local reasoning buffer and detection state while retaining the
        request-scoped conversation history used for comparisons.

        Args:
            preserve_buffer: Ignored for RLS — always clears local buffer.
        """
        self._reasoning_buffer = ""
        self._reasoning_deltas.clear()
        self._last_reasoning_from_conversation = self._conversation_reasoning
        self._tool_call_signatures.clear()
        self._has_tool_call = False
        self._decision = None
        self._finalized = False
        if recovery_attempt:
            self._attempt_index += 1

    def process_event(self, event: StreamEvent) -> list[StreamEvent]:
        """Process a single StreamEvent.

        **Observer semantics:**
        - ReasoningTextDelta → copy into buffer, return ``[event]``
          (pass-through).
        - ToolCallDelta / ToolCallComplete → track signatures for
          disambiguation, return ``[event]`` (pass-through).
        - All other events → return ``[event]`` (pass-through).

        Never returns ``[]`` — RLSFinalizer is an observer, not a consumer.
        """
        if isinstance(event, ReasoningTextDelta):
            self._buffer_reasoning(event.delta)
            return [event]

        if isinstance(event, (ToolCallDelta, ToolCallComplete)):
            self._buffer_tool_signature(event)
            self._has_tool_call = True
            return [event]

        # Pass through all other events (AssistantTextDelta, Finish, Done,
        # Keepalive, Error, etc.)
        return [event]

    def finalize(self, global_attempt_index: int = 0) -> list[StreamEvent]:
        """Finalize before ``Finish`` is emitted.

        Checks for loop patterns in the buffered reasoning. If a loop is
        detected and max_attempts is not exceeded, produces a
        ``RecoveryDecision``.

        Args:
            global_attempt_index: Global recovery attempt counter passed by
                the runner.

        Returns
        -------
        list[StreamEvent]
            Empty list — RLSFinalizer does not emit events. Recovery is
            signaled via the ``decision`` property.
        """
        if self._finalized:
            raise RuntimeError("RLSFinalizer.finalize() already called")
        self._finalized = True

        # Check for loops.
        if self._detect_loop():
            # Check max_attempts
            if self._attempt_index >= self.max_attempts:
                self._decision = None
            else:
                self._decision = self._build_recovery_decision(
                    global_attempt_index=global_attempt_index,
                )
        else:
            self._decision = None

        return []

    # ── Public inspection helpers (for tests) ─────────────────────

    @property
    def decision(self) -> Optional[RecoveryDecision]:
        """The RecoveryDecision produced by finalize(), or None."""
        return self._decision

    @property
    def has_tool_call(self) -> bool:
        """Whether any tool call event was seen during processing."""
        return self._has_tool_call

    @property
    def attempt_index(self) -> int:
        """Per-finalizer attempt counter for recovery."""
        return self._attempt_index

    @property
    def recovery_exhausted(self) -> bool:
        """Whether a detected reasoning loop exhausted this finalizer's budget."""
        return self._finalized and self._detect_loop() and (
            self._attempt_index >= self.max_attempts
        )

    @property
    def reasoning_buffer(self) -> str:
        """The accumulated reasoning text (for test inspection)."""
        return self._reasoning_buffer

    # ── Internal helpers ──────────────────────────────────────────

    def _buffer_reasoning(self, delta: str) -> None:
        """Buffer a reasoning text delta.

        Appends the delta to the local buffer.
        """
        self._reasoning_buffer += delta
        self._reasoning_deltas.append(delta)

    def _buffer_tool_signature(self, event: StreamEvent) -> None:
        """Buffer a tool call signature for disambiguation.

        Parameters
        ----------
        event:
            ToolCallDelta or ToolCallComplete event.
        """
        from keeprollming.filters.tool_loop_stopper.stream import _tool_call_signature
        sig = _tool_call_signature(event)
        if sig is not None:
            self._tool_call_signatures.append(sig)

    @staticmethod
    def _signatures_from_history(
        tool_calls: Sequence[Mapping[str, Any]],
    ) -> List[str]:
        """Normalize historical OpenAI tool calls for disambiguation."""
        signatures: List[str] = []
        for tool_call in tool_calls:
            function = tool_call.get("function")
            if not isinstance(function, Mapping):
                continue
            name = function.get("name")
            arguments = function.get("arguments", "")
            if not isinstance(name, str) or not isinstance(arguments, str):
                continue
            from keeprollming.filters.tool_loop_stopper.stream import _tool_call_signature
            signature = _tool_call_signature(
                ToolCallComplete(
                    index=int(tool_call.get("index", 0)),
                    id=str(tool_call.get("id", "")),
                    name=name,
                    arguments_json=arguments,
                )
            )
            if signature is not None:
                signatures.append(signature)
        return signatures

    def _detect_loop(self) -> bool:
        """Detect if a reasoning-loop pattern exists.

        Detection strategies:
        1. Identical reasoning text compared with the last reasoning from
           the current conversation history.
        2. Tool-call disambiguation: same reasoning + different tool calls
           = not a loop.
        3. (Experimental, opt-in) Within-stream repeated-suffix detection:
           detects when the model repeats the same reasoning multiple times
           in a single response. Disabled by default due to potential
           false positives with tool-call disambiguation.

        Returns
        -------
        bool
            True if a loop is detected, False otherwise.
        """
        if not self._reasoning_buffer:
            return False

        current_reasoning = self._reasoning_buffer

        # Experimental: within-stream repeated-suffix detection (opt-in)
        if self.detect_within_stream_loop:
            if len(self._reasoning_deltas) >= 2 and len(current_reasoning) >= 10:
                half = len(current_reasoning) // 2
                first_half = current_reasoning[:half].strip()
                second_half = current_reasoning[half:].strip()
                if first_half and first_half == second_half:
                    # Check tool-call disambiguation
                    if not (self._has_tool_call and
                            self._tool_call_signatures and
                            not self._reasoning_matches_with_tool_disambiguation(
                                current_reasoning, first_half)):
                        return True

        # Get the last reasoning from the current conversation history.
        last_reasoning = self._get_last_reasoning()

        if last_reasoning is not None:
            # Tool-call disambiguation: same reasoning + different tool calls
            # = not a loop
            if self._has_tool_call and self._tool_call_signatures:
                if not self._reasoning_matches_with_tool_disambiguation(
                    current_reasoning, last_reasoning
                ):
                    return False

            # Check for identical reasoning
            if current_reasoning == last_reasoning:
                return True

        return False

    def _get_last_reasoning(self) -> Optional[str]:
        """Get the last reasoning text from conversation history.

        Returns
        -------
        Optional[str]
            The last reasoning text, or None if not available.
        """
        return self._last_reasoning_from_conversation

    def _reasoning_matches_with_tool_disambiguation(
        self,
        reasoning1: str,
        reasoning2: str,
    ) -> bool:
        """Check if reasoning matches, considering tool-call disambiguation.

        If the reasoning text is identical but the tool call signatures
        are different, this is NOT a loop (the model is reasoning about
        different tools).

        Parameters
        ----------
        reasoning1:
            Current reasoning text.
        reasoning2:
            Last reasoning text.

        Returns
        -------
        bool
            True if the reasoning matches AND tool calls are the same
            (indicating a loop), False otherwise.
        """
        if reasoning1 != reasoning2:
            return False

        # Reasoning is identical — check tool calls for disambiguation
        if not self._tool_call_signatures:
            # No tool calls — identical reasoning = loop
            return True

        # When the preceding assistant turn has tool calls, compare the
        # current call set with that turn.  Same reasoning for a different
        # action is legitimate; same reasoning for the same action is a loop.
        if self._conversation_tool_signatures:
            return set(self._tool_call_signatures) == set(
                self._conversation_tool_signatures
            )

        # Without historical tool calls, multiple distinct calls in this
        # response disambiguate an otherwise repeated reasoning block.
        unique_sigs = set(self._tool_call_signatures)
        if len(unique_sigs) > 1:
            # Different tool calls — not a loop
            return False

        # Same tool calls — identical reasoning = loop
        return True

    def _build_recovery_decision(
        self,
        global_attempt_index: int,
    ) -> RecoveryDecision:
        """Build a RecoveryDecision for the detected reasoning loop.

        Parameters
        ----------
        global_attempt_index:
            Global recovery attempt counter.

        Returns
        -------
        RecoveryDecision
            Intervention decision with reasoning nudge.
        """
        return RecoveryDecision(
            kind="intervention",
            reason=(
                "Reasoning-loop detected: identical reasoning text repeated. "
                "Injecting user nudge to break the loop."
            ),
            priority=self.priority,
            origin_finalizer="RLSFinalizer",
            attempt_index=self._attempt_index,
            max_attempts=self.max_attempts,
            global_attempt_index=global_attempt_index,
            request_payload_patch={
                "messages": [
                    {
                        "role": "user",
                        "content": self.nudge_message,
                    },
                ],
            },
            preserve_output_so_far=True,
            merge_strategy="intervention_specific",
            diagnostics={
                "reasoning_buffer": self._reasoning_buffer,
                "last_reasoning": self._get_last_reasoning(),
            },
        )
