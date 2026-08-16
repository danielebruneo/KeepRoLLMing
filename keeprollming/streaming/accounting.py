"""ExecutionUsage accounting dataclasses for canonical streaming pipeline.

This module provides the data structures for tracking actual upstream
execution usage across multiple attempts, independent of client-facing
usage metadata.

Design:
- `AttemptUsage` captures per-attempt raw usage from the provider.
- `ExecutionUsage` aggregates across all attempts to produce cumulative
  accounting truth for metrics, cost tracking, and retry analysis.

Contract Rules (from D-035):
- Rule 1 — Partial field semantics: Missing fields (absent keys OR `null`
  values) are treated as 0 for SUM purposes. This is an **aggregation
  operation**, not an assertion that the provider semantically reported zero.
- Rule 2 — Total tokens derivation: `upstream_total_tokens =
  upstream_prompt_tokens + upstream_completion_tokens` is a KRM-derived
  aggregate. Provider-reported
  `total_tokens` is **never** used for aggregation.
- Rule 4 — Fallback path accounting: Fallback path does NOT create an
  `AttemptUsage` entry.
- Rule 5 — Recovery reset timing: The runner reads `parser.pending_usage[0]`
  BEFORE the parser resets it.

Usage:
    from keeprollming.streaming.accounting import ExecutionUsage, AttemptUsage

    # Create accounting object
    usage = ExecutionUsage.empty()

    # At attempt completion (in runner.py)
    usage.add_attempt(attempt_index, raw_usage)

    # After all attempts
    usage.finalize()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AttemptUsage:
    """Per-attempt raw usage capture.

    Attributes:
        attempt_index: Monotonic attempt counter (0-based).
        raw_usage: Verbatim provider usage dict, or None if no usage was
            reported for this attempt.
    """

    attempt_index: int
    raw_usage: Optional[Dict[str, Any]]


@dataclass
class ExecutionUsage:
    """Cumulative execution accounting across all upstream attempts.

    This is the Phase 1 internal accounting object. It is request-scoped,
    runner-owned, and parser-populated via shared mutable
    ``_pending_usage[0]`` container.

    Attributes:
        upstream_attempts: Total number of upstream iterators consumed.
            Counts consumed iterators, not factory calls. Fallback path
            (upstream_factory exception) does NOT increment this counter.
        usage_reported_attempts: Number of attempts where the provider
            actually reported usage metadata (raw_usage is not None).
        upstream_prompt_tokens: Cumulative prompt tokens across all attempts.
            Aggregated using Rule 1 (missing/null fields treated as 0).
        upstream_completion_tokens: Cumulative completion tokens across all
            attempts.
            Aggregated using Rule 1.
        upstream_total_tokens: Derived aggregate of upstream token totals.
            Provider-reported total_tokens is never used for aggregation
            (Rule 2).
        usage_complete: True if usage_reported_attempts == upstream_attempts.
            Indicates all attempts reported usage metadata.
        finish_reason: Final finish_reason from the upstream stream
            (e.g. "stop", "tool_calls"), or None if not determined.
        final_prompt_tokens: Prompt tokens for the final logical request.
        final_cached_prompt_tokens: Prompt tokens served from the provider's
            KV cache for the final logical request, when reported.
        final_completion_tokens: Tokens visible in the final client result.
        attempts: Per-attempt ledger of raw usage captures.
    """

    upstream_attempts: int = 0
    usage_reported_attempts: int = 0
    upstream_prompt_tokens: int = 0
    upstream_completion_tokens: int = 0
    upstream_total_tokens: int = 0
    final_prompt_tokens: Optional[int] = None
    final_cached_prompt_tokens: Optional[int] = None
    final_completion_tokens: Optional[int] = None
    usage_complete: bool = False
    finish_reason: Optional[str] = None
    attempts: List[AttemptUsage] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "ExecutionUsage":
        """Create an empty ExecutionUsage with zero-attempt state.

        Returns:
            ExecutionUsage with all counters at zero and empty attempts list.
        """
        return cls(
            upstream_attempts=0,
            usage_reported_attempts=0,
            upstream_prompt_tokens=0,
            upstream_completion_tokens=0,
            upstream_total_tokens=0,
            usage_complete=True,  # 0 == 0
            attempts=[],
        )

    def add_attempt(
        self,
        attempt_index: int,
        raw_usage: Optional[Dict[str, Any]],
    ) -> None:
        """Add a per-attempt usage capture to the accounting object.

        Implements upstream accounting Rules 1 and 2:
        - Rule 1: Missing fields (absent keys OR null values) are treated
          as 0 for SUM purposes.
        - Rule 2: upstream_total_tokens is derived from prompt + completion, not
          taken from provider-reported total_tokens.

        Args:
            attempt_index: The attempt index (monotonic, 0-based).
            raw_usage: Verbatim provider usage dict, or None if no usage
                was reported.
        """
        # Create per-attempt ledger entry
        attempt = AttemptUsage(
            attempt_index=attempt_index,
            raw_usage=raw_usage,
        )
        self.attempts.append(attempt)

        # Increment upstream attempt counter
        self.upstream_attempts += 1

        # If usage was reported, aggregate tokens
        if raw_usage is not None:
            self.usage_reported_attempts += 1

            # Rule 1: Partial field semantics
            # Missing fields (absent keys OR null values) treated as 0
            prompt = raw_usage.get("prompt_tokens", 0) or 0
            completion = raw_usage.get("completion_tokens", 0) or 0
            prompt_details = raw_usage.get("prompt_tokens_details") or {}
            cached_prompt = (
                prompt_details.get("cached_tokens")
                if isinstance(prompt_details, dict) else None
            )
            try:
                cached_prompt = int(cached_prompt) if cached_prompt is not None else None
            except (TypeError, ValueError):
                cached_prompt = None

            self.upstream_prompt_tokens += prompt
            self.upstream_completion_tokens += completion
            # This provisional value is replaced by the endpoint once it has
            # reconstructed the final client-visible transcript.
            self.final_prompt_tokens = prompt
            self.final_cached_prompt_tokens = cached_prompt
            self.final_completion_tokens = completion

        # Rule 2: derive upstream total from upstream prompt + completion.
        self.upstream_total_tokens = (
            self.upstream_prompt_tokens + self.upstream_completion_tokens
        )

        # Update usage_complete flag
        self.usage_complete = (
            self.usage_reported_attempts == self.upstream_attempts
        )

    def finalize(self) -> None:
        """Finalize the accounting object.

        Ensures all counters are consistent and usage_complete flag is
        up-to-date. This is called after all attempts have been captured
        (at stream exhaustion or final Finish).

        Note: This method recomputes usage_complete from current counters,
        which is useful when add_attempt() was called but usage_complete
        was not updated (e.g., when add_attempt() doesn't update it).
        """
        self.usage_complete = (
            self.usage_reported_attempts == self.upstream_attempts
        )

    def set_final_usage(
        self,
        *,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> None:
        """Record tokens for the final logical client result.

        Unlike cumulative upstream totals, these values describe the request
        and response seen by the client after recovery/finalization.
        """
        self.final_prompt_tokens = prompt_tokens
        self.final_completion_tokens = completion_tokens

    @property
    def prompt_tokens(self) -> int:
        """Compatibility alias for cumulative upstream prompt tokens."""
        return self.upstream_prompt_tokens

    @property
    def completion_tokens(self) -> int:
        """Compatibility alias for cumulative upstream completion tokens."""
        return self.upstream_completion_tokens

    @property
    def total_tokens(self) -> int:
        """Compatibility alias for cumulative upstream total tokens."""
        return self.upstream_total_tokens

    @property
    def recovery_count(self) -> int:
        """Number of recovery attempts (attempts beyond the first).

        Returns:
            Number of recovery attempts (0 if only one attempt was made).
        """
        return max(0, self.upstream_attempts - 1)

    @property
    def retry_amplification_ratio(self) -> float:
        """Ratio of upstream attempts to usage-reported attempts.

        Returns:
            Ratio as a float. Returns 0.0 if no attempts were made.
            Returns float('inf') if upstream_attempts > 0 but
            usage_reported_attempts == 0 (unbounded amplification).
        """
        if self.upstream_attempts == 0:
            return 0.0
        if self.usage_reported_attempts == 0:
            return float("inf")
        return self.upstream_attempts / self.usage_reported_attempts
