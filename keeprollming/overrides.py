"""Route-level parameter overrides.

Allows a route config to specify model parameters that override whatever
the downstream client sends.  Only a whitelist of safe parameters is applied.

Example route config::

    overrides:
        temperature: 0.9
        top_p: 0.95
        max_tokens: 4096

When present, these values replace the corresponding fields in the upstream
payload *before* any summarisation or clamping logic runs.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Whitelist of parameters that can be overridden at route level.
# These map directly to OpenAI chat-completions top-level fields.
ALLOWED_OVERRIDE_KEYS = frozenset({
    "temperature",
    "top_p",
    "max_tokens",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "seed",
    "min_p",
    "repetition_penalty",
    # OpenAI-compatible reasoning control.  LibreChat sends this as a
    # top-level request field, and Qwen-compatible gateways may use it to
    # select the model's thinking intensity.
    "reasoning_effort",
})


def apply_overrides(
    payload: Dict[str, Any],
    overrides: Optional[Dict[str, Any]],
) -> list[Tuple[str, Any, Any]]:
    """Apply route-level overrides to a copy of the upstream payload.

    Parameters are replaced **unconditionally** — it does not matter whether
    the downstream client sent them or not.

    Args:
        payload: The (shallow-copied) upstream payload dict to mutate in place.
        overrides: Dict of parameter overrides from route config, or ``None``.

    Returns:
        A list of ``(key, old_value, new_value)`` tuples for every override
        that was actually applied.  Empty list means no changes.
    """
    if not overrides:
        return []

    applied: list[Tuple[str, Any, Any]] = []

    for key, new_val in overrides.items():
        if key not in ALLOWED_OVERRIDE_KEYS:
            continue  # silently skip unknown keys

        old_val = payload.get(key)
        if old_val == new_val:
            continue  # no-op — same value

        payload[key] = new_val
        applied.append((key, old_val, new_val))

    return applied
