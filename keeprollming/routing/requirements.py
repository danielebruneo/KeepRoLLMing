"""Static request requirements for deterministic balanced-route eligibility.

This first P5 policy uses only OpenAI-compatible request fields and
operator-declared route capabilities. It deliberately remains advisory for
tokenisation: the existing post-filter upstream clamp is still the final
context-safety boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from ..token_counter import TokenCounter

_ROUTED_CAPABILITIES = frozenset({"tools", "vision", "reasoning"})
_DISABLED_THINKING_VALUES = frozenset({"", "0", "false", "none", "off"})
_IMAGE_CONTEXT_TOKEN_ESTIMATE = 1024


@dataclass(frozen=True)
class RequestRequirements:
    """Non-identifying request facts usable by a balance predicate."""

    capabilities: frozenset[str]
    prompt_tokens_est: int | None
    requested_completion_tokens: int | None
    image_count: int = 0

    def incompatibility_reason(
        self,
        route: Any,
        *,
        safety_margin_tokens: int = 128,
        enforce_capabilities: bool = True,
    ) -> str | None:
        """Return one stable exclusion reason, or ``None`` when compatible."""
        if enforce_capabilities:
            declared = _normalized_capabilities(getattr(route, "capabilities", None))
            missing = sorted(self.capabilities - declared)
            if missing:
                return f"missing_capability:{missing[0]}"
        if self.prompt_tokens_est is None:
            return None
        context_window = _positive_int(getattr(route, "ctx_len", None))
        if context_window is None:
            return None
        completion = self.requested_completion_tokens
        if completion is None:
            completion = _positive_int(getattr(route, "max_tokens", None)) or 0
        required = self.prompt_tokens_est + completion + max(0, safety_margin_tokens)
        return "context_insufficient" if required > context_window else None

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        return tuple(sorted(self.capabilities))


def derive_request_requirements(
    payload: Mapping[str, Any],
    *,
    route_overrides: Mapping[str, Any] | None = None,
) -> RequestRequirements:
    """Extract effective capability and context facts without retaining text.

    Route overrides are applied to upstream payloads after balance selection.
    Consider their relevant values here as well, otherwise a selector could
    choose a target that cannot honour its own route's reasoning or output
    budget policy.
    """
    effective_payload: Mapping[str, Any] = payload
    if route_overrides:
        effective_payload = dict(payload)
        for key in ("reasoning_effort", "max_tokens"):
            if key in route_overrides:
                effective_payload[key] = route_overrides[key]

    messages = payload.get("messages")
    message_list = (
        [message for message in messages if isinstance(message, Mapping)] if isinstance(messages, list) else []
    )
    image_count = _count_images(message_list)
    capabilities: set[str] = set()
    if isinstance(payload.get("tools"), list) and payload["tools"]:
        capabilities.add("tools")
    if image_count:
        capabilities.add("vision")
    if _thinking_requested(effective_payload):
        # ``reasoning`` is KRM's established static capability name.  The
        # accepted upstream controls retain their provider-facing names.
        capabilities.add("reasoning")

    prompt_tokens_est: int | None = None
    if isinstance(messages, list):
        try:
            prompt_tokens_est = TokenCounter().count_messages(message_list)
            prompt_tokens_est += image_count * _IMAGE_CONTEXT_TOKEN_ESTIMATE
        except Exception:  # pragma: no cover - defensive legacy-payload guard
            prompt_tokens_est = None
    return RequestRequirements(
        capabilities=frozenset(capabilities & _ROUTED_CAPABILITIES),
        prompt_tokens_est=prompt_tokens_est,
        requested_completion_tokens=_requested_completion_tokens(effective_payload),
        image_count=image_count,
    )


def _normalized_capabilities(value: Iterable[Any] | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    return frozenset(item.strip().lower() for item in value if isinstance(item, str) and item.strip())


def _thinking_requested(payload: Mapping[str, Any]) -> bool:
    if payload.get("enable_thinking") is True or payload.get("thinking") is True:
        return True
    effort = payload.get("reasoning_effort")
    return isinstance(effort, str) and effort.strip().lower() not in _DISABLED_THINKING_VALUES


def _count_images(messages: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, Mapping):
                continue
            part_type = str(part.get("type", "")).lower()
            if part_type in {"image_url", "input_image", "image"} or "image_url" in part:
                count += 1
    return count


def _requested_completion_tokens(payload: Mapping[str, Any]) -> int | None:
    for key in ("max_tokens", "max_completion_tokens"):
        value = _positive_int(payload.get(key))
        if value is not None:
            return value
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value > 0 else None


__all__ = ["RequestRequirements", "derive_request_requirements"]
