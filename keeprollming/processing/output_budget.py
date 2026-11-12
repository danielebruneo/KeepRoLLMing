"""Shared output-token selection and context-room clamping."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class OutputTokenBudgetError(ValueError):
    """A request has no valid output budget."""


@dataclass(frozen=True)
class OutputTokenBudget:
    requested: int
    maximum: int


def resolve_output_token_budget(
    *,
    client_max_tokens: Any,
    default_max_tokens: int,
    context_window: int,
    safety_margin: int,
    prompt_tokens: int = 0,
    overrides: Mapping[str, Any] | None = None,
) -> OutputTokenBudget:
    """Overrides > client > resolved default, bounded by remaining context.

    Planning uses prompt_tokens=0 to reserve output before summarization.
    Payload construction supplies the repacked prompt estimate. Never invent
    a minimum of 64 tokens when less context remains, or restore a superseded
    client value after applying overrides.
    """
    requested = client_max_tokens if client_max_tokens is not None else default_max_tokens
    if overrides and "max_tokens" in overrides:
        requested = overrides["max_tokens"]
    if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
        raise OutputTokenBudgetError("max_tokens must be a positive integer")
    available = int(context_window) - int(prompt_tokens) - int(safety_margin)
    if available <= 0:
        raise OutputTokenBudgetError("The prompt leaves no room for output within the configured context window")
    return OutputTokenBudget(requested=requested, maximum=min(requested, available))
