"""TLSFinalizer — Tool-Loop Stopper for canonical streaming pipeline.

Detects tool-loop patterns in streaming output and produces a
``RecoveryDecision`` with ``kind="intervention"`` and
``merge_strategy="inject_tool_result"`` so the runner can inject a
tool-result message and break the loop.

**Observer semantics:** TLSFinalizer copies ToolCallDelta/ToolCallComplete
events into its internal buffer for detection but **returns the original
event** (not ``[]``). It does NOT own ToolCallComplete assembly and does
NOT interfere with ToolCallFinalizer or I9.

This module is **independent** of the previous pipeline and can be unit-tested
in isolation.
"""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _canonical_json(value: Any) -> str:
    """Return a canonical JSON string (sorted keys, no whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _tool_call_signature(event: StreamEvent) -> Optional[str]:
    """Build a signature for a tool call event.

    For ToolCallDelta: function name + canonical JSON args (sorted keys).
    For ToolCallComplete: function name + canonical JSON arguments_obj.

    Returns None if the event is not a tool call or cannot be signed.
    """
    if isinstance(event, ToolCallDelta):
        if not event.name:
            return None
        args = ""
        if event.arguments_delta:
            try:
                args_obj = json.loads(event.arguments_delta)
                args = _canonical_json(args_obj)
            except (json.JSONDecodeError, ValueError):
                args = event.arguments_delta
        return f"{event.name}::{args}"
    elif isinstance(event, ToolCallComplete):
        if not event.name:
            return None
        if event.arguments_obj is not None:
            return f"{event.name}::{_canonical_json(event.arguments_obj)}"
        elif event.arguments_json:
            return f"{event.name}::{event.arguments_json}"
        return event.name
    return None


def _fuzzy_match(sig1: str, sig2: str, threshold: float = 0.8) -> bool:
    """Check if two signatures are fuzzily similar.

    Simple character-level similarity ratio (Levenshtein-based approximation
    using sequence matching). Returns True if similarity >= threshold.
    """
    if sig1 == sig2:
        return True
    if not sig1 or not sig2:
        return False

    # Use difflib for simplicity
    from difflib import SequenceMatcher
    ratio = SequenceMatcher(None, sig1, sig2).ratio()
    return ratio >= threshold


def _build_tool_result_message(
    tool_name: str,
    tool_call_id: str,
    tls_message: str,
) -> Dict[str, Any]:
    """Build a tool-role message for the request_payload_patch.

    Parameters
    ----------
    tool_name:
        The tool function name that was looped.
    tool_call_id:
        The tool call ID to match in the conversation.
    tls_message:
        The user message to break the loop.

    Returns
    -------
    dict
        A message dict suitable for insertion into request_payload_patch.
    """
    # Include tool name in content for clarity
    content = f"[{tool_name}] {tls_message}"
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": content,
    }


def _build_user_nudge_message(tool_name: str) -> Dict[str, Any]:
    """Build a user-role nudge message to break the tool loop.

    Parameters
    ----------
    tool_name:
        The tool function name that was looped.

    Returns
    -------
    dict
        A message dict suitable for insertion into request_payload_patch.
    """
    return {
        "role": "user",
        "content": (
            f"Do NOT call `{tool_name}()` again. Provide a direct answer "
            "without invoking the tool."
        ),
    }


# ---------------------------------------------------------------------------
# TLSFinalizer
# ---------------------------------------------------------------------------


class TLSFinalizer(StreamFinalizer):
    """Detect tool-loop patterns and produce intervention recovery decisions.

    **Observer semantics:**
    - ToolCallDelta / ToolCallComplete events are copied into the TLS buffer
      for detection purposes.
    - The original event is **always returned** (``[event]``), never consumed.
    - Does NOT own ToolCallComplete assembly (ToolCallFinalizer does).
    - Does NOT interfere with I9 invariant.

    **Loop detection:**
    - Exact consecutive repeated tool call signatures.
    - Fuzzy repeated signatures (if ``fuzzy_threshold`` is configured).
    - AB-loop (if ``detect_ab_loop`` is True): alternating between two
      distinct tool signatures.

    **On loop detection:**
    - Creates a ``RecoveryDecision(kind="intervention")`` with
      ``merge_strategy="inject_tool_result"``.
    - ``request_payload_patch`` contains conversation augmentation:
      - A tool-role message with matching ``tool_call_id`` and TLS message.
      - An optional user-role message warning against re-calling the tool.

    **On no loop:**
    - ``decision`` is ``None``.
    - No output mutation — original events pass through unchanged.

    **Max attempts:**
    - If ``attempt_index >= max_attempts``, the finalizer does NOT produce
      a decision. The fallback/recovery exhaustion is handled by C2 runner
      integration.

    Priority: 55 — runs after ToolCallFinalizer (40) and before RLS (60).

    Parameters
    ----------
    max_attempts:
        Maximum recovery attempts before giving up. Default ``3``.
    fuzzy_threshold:
        Similarity ratio for fuzzy signature matching (0.0-1.0). Default
        ``None`` disables fuzzy matching.
    detect_ab_loop:
        If True, detect AB-loop patterns (alternating two distinct
        signatures). Default ``False``.
    tls_message:
        Message to inject as tool result to break the loop. Default
        ``"Tool result: please provide a direct answer without calling tools."``
    nudge_message:
        User message to warn against re-calling the tool. Default
        ``"Do NOT call the tool again. Provide a direct answer."``
    fallback_message:
        User-visible message emitted if the loop cannot be recovered.
    """

    priority: int = 55

    def __init__(
        self,
        max_attempts: int = 3,
        fuzzy_threshold: Optional[float] = None,
        detect_ab_loop: bool = False,
        tls_message: str = (
            "Tool result: please provide a direct answer without "
            "calling tools."
        ),
        nudge_message: str = (
            "Do NOT call the tool again. Provide a direct answer."
        ),
        fallback_message: str | None = None,
        conversation_tool_calls: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.max_attempts = max_attempts
        self.fuzzy_threshold = fuzzy_threshold
        self.detect_ab_loop = detect_ab_loop
        self.tls_message = tls_message
        self.nudge_message = nudge_message
        self.fallback_message = fallback_message

        # Tool call buffer for loop detection
        self._tool_signatures: List[str] = []
        self._tool_call_ids: List[str] = []
        self._tool_names: List[str] = []
        self._has_tool_call = False
        self._decision: Optional[RecoveryDecision] = None
        self._finalized = False
        self._attempt_index = 0
        self._history_tool_events = tuple(conversation_tool_calls or ())
        self._restore_conversation_history()

    # ── StreamFinalizer contract ──────────────────────────────────

    def reset(
        self,
        preserve_buffer: bool = True,
        recovery_attempt: bool = True,
    ) -> None:
        """Reset finalizer state for a new recovery attempt.

        Clears the tool buffer and detection state. Does NOT affect
        ToolCallFinalizer state.

        Args:
            preserve_buffer: Ignored for TLS — always clears buffer.
        """
        self._tool_signatures.clear()
        self._tool_call_ids.clear()
        self._tool_names.clear()
        self._restore_conversation_history()
        self._has_tool_call = False
        self._decision = None
        self._finalized = False
        if recovery_attempt:
            self._attempt_index += 1

    def _restore_conversation_history(self) -> None:
        """Seed loop detection from already-completed conversation turns."""
        for tool_call in self._history_tool_events:
            function = tool_call.get("function")
            if not isinstance(function, Mapping):
                continue
            name = function.get("name")
            arguments = function.get("arguments", "")
            if not isinstance(name, str) or not isinstance(arguments, str):
                continue
            event = ToolCallComplete(
                index=int(tool_call.get("index", 0)),
                id=str(tool_call.get("id", "")),
                name=name,
                arguments_json=arguments,
            )
            self._buffer_tool_event(event)

    def process_event(self, event: StreamEvent) -> list[StreamEvent]:
        """Process a single StreamEvent.

        **Observer semantics:**
        - ToolCallDelta / ToolCallComplete → copy into buffer, return
          ``[event]`` (pass-through).
        - All other events → return ``[event]`` (pass-through).

        Never returns ``[]`` — TLSFinalizer is an observer, not a consumer.
        """
        if isinstance(event, (ToolCallDelta, ToolCallComplete)):
            self._buffer_tool_event(event)
            self._has_tool_call = True
            return [event]

        # Pass through all other events (AssistantTextDelta, ReasoningTextDelta,
        # Finish, Done, Keepalive, Error, etc.)
        return [event]

    def finalize(self, global_attempt_index: int = 0) -> list[StreamEvent]:
        """Finalize before ``Finish`` is emitted.

        Checks for loop patterns in the buffered tool calls. If a loop is
        detected and max_attempts is not exceeded, produces a
        ``RecoveryDecision``.

        Args:
            global_attempt_index: Global recovery attempt counter passed by
                the runner.

        Returns
        -------
        list[StreamEvent]
            Empty list — TLSFinalizer does not emit events. Recovery is
            signaled via the ``decision`` property.
        """
        if self._finalized:
            raise RuntimeError("TLSFinalizer.finalize() already called")
        self._finalized = True

        # Check for loops
        if self._detect_loop():
            # Check max_attempts
            if self._attempt_index >= self.max_attempts:
                self._decision = None
                return []

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
        """Whether a detected tool loop exhausted this finalizer's budget."""
        return self._finalized and self._detect_loop() and (
            self._attempt_index >= self.max_attempts
        )

    @property
    def tool_signatures(self) -> List[str]:
        """The buffered tool signatures (for test inspection)."""
        return list(self._tool_signatures)

    # ── Internal helpers ──────────────────────────────────────────

    def _buffer_tool_event(self, event: StreamEvent) -> None:
        """Buffer a tool call event for loop detection.

        Extracts the signature and tool_call_id from the event and appends
        them to the internal buffers.
        """
        sig = _tool_call_signature(event)
        if sig is not None:
            self._tool_signatures.append(sig)

        if isinstance(event, ToolCallDelta):
            if event.id:
                self._tool_call_ids.append(event.id)
            if event.name:
                self._tool_names.append(event.name)
        elif isinstance(event, ToolCallComplete):
            if event.id:
                self._tool_call_ids.append(event.id)
            if event.name:
                self._tool_names.append(event.name)

    def _detect_loop(self) -> bool:
        """Detect if a tool-loop pattern exists in the buffer.

        Detection strategies:
        1. Exact consecutive repeated signatures.
        2. Fuzzy repeated signatures (if configured).
        3. AB-loop (if configured): alternating between two distinct
           signatures.

        Returns
        -------
        bool
            True if a loop is detected, False otherwise.
        """
        if len(self._tool_signatures) < 2:
            return False

        signatures = self._tool_signatures

        # 1. Exact consecutive repeated signatures
        for i in range(1, len(signatures)):
            if signatures[i] == signatures[i - 1]:
                return True

        # 2. Fuzzy repeated signatures
        if self.fuzzy_threshold is not None:
            for i in range(1, len(signatures)):
                if _fuzzy_match(signatures[i], signatures[i - 1],
                                self.fuzzy_threshold):
                    return True

        # 3. AB-loop detection
        if self.detect_ab_loop and len(signatures) >= 4:
            # Check for pattern A B A B
            for i in range(len(signatures) - 3):
                if (signatures[i] == signatures[i + 2] and
                        signatures[i + 1] == signatures[i + 3] and
                        signatures[i] != signatures[i + 1]):
                    return True

        return False

    def _build_recovery_decision(
        self,
        global_attempt_index: int,
    ) -> RecoveryDecision:
        """Build a RecoveryDecision for the detected loop.

        Parameters
        ----------
        global_attempt_index:
            Global recovery attempt counter.

        Returns
        -------
        RecoveryDecision
            Intervention decision with tool-result injection.
        """
        # Get the repeated signature
        last_sig = self._tool_signatures[-1]
        prev_sig = self._tool_signatures[-2] if len(self._tool_signatures) >= 2 else ""

        # Extract tool name from signature
        tool_name = last_sig.split("::")[0] if "::" in last_sig else last_sig

        # Get the tool_call_id (use the last one if available)
        tool_call_id = (
            self._tool_call_ids[-1] if self._tool_call_ids else "unknown_tool_call_id"
        )

        # Build request_payload_patch
        tool_result_msg = _build_tool_result_message(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tls_message=self.tls_message,
        )
        nudge_msg = _build_user_nudge_message(tool_name=tool_name)

        return RecoveryDecision(
            kind="intervention",
            reason=(
                f"Tool-loop detected: repeated call to '{tool_name}'. "
                f"Injecting tool result to break the loop."
            ),
            priority=self.priority,
            origin_finalizer="TLSFinalizer",
            attempt_index=self._attempt_index,
            max_attempts=self.max_attempts,
            global_attempt_index=global_attempt_index,
            request_payload_patch={
                "messages": [tool_result_msg, nudge_msg],
            },
            preserve_output_so_far=True,
            merge_strategy="inject_tool_result",
            diagnostics={
                "loop_type": self._detect_loop_type(),
                "tool_signature": last_sig,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "signatures": self._tool_signatures,
            },
        )

    def _detect_loop_type(self) -> str:
        """Detect the type of loop pattern.

        Returns
        -------
        str
            One of "exact_consecutive", "fuzzy", "ab_loop", "unknown".
        """
        signatures = self._tool_signatures
        if len(signatures) < 2:
            return "unknown"

        # Check exact consecutive
        for i in range(1, len(signatures)):
            if signatures[i] == signatures[i - 1]:
                return "exact_consecutive"

        # Check fuzzy
        if self.fuzzy_threshold is not None:
            for i in range(1, len(signatures)):
                if _fuzzy_match(signatures[i], signatures[i - 1],
                                self.fuzzy_threshold):
                    return "fuzzy"

        # Check AB-loop
        if self.detect_ab_loop and len(signatures) >= 4:
            for i in range(len(signatures) - 3):
                if (signatures[i] == signatures[i + 2] and
                        signatures[i + 1] == signatures[i + 3] and
                        signatures[i] != signatures[i + 1]):
                    return "ab_loop"

        return "unknown"
