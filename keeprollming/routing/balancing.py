"""Validated, deterministic route selection for ``balance.targets``.

The module intentionally contains no HTTP, admission or cache implementation.
It compiles a balance policy into independently resolved route destinations so
the endpoint can make one observable execution decision without accidentally
mixing selector settings into a backend target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from ..overrides import ALLOWED_OVERRIDE_KEYS
from ..types import DefaultSettings, Route
from .router import (
    RerouteResolution,
    RouteRerouteError,
    resolve_fallback_chain,
    resolve_inherited_route,
    resolve_reroute_destination,
)


@dataclass(frozen=True)
class BalanceReservationPolicy:
    """Source-route policy for affinity lifetime and idle resource leasing."""

    affinity_ttl_seconds: float = 60 * 60
    enabled: bool = True
    mode: str = "priority"
    timeout_seconds: float | None = None
    min_prompt_tokens: int = 0
    min_cached_prompt_tokens: int = 0
    min_seconds: float = 60.0
    max_seconds: float = 15 * 60.0
    min_context_ratio: float = 0.10
    max_context_ratio: float = 0.75
    promote_when_beneficial: bool = True


@dataclass(frozen=True)
class BalanceWaitPolicy:
    """How a selector behaves while every otherwise-valid target is busy."""

    retry_interval_seconds: float = 0.25
    allow_reservation_bypass: bool = True


@dataclass(frozen=True)
class CompactionHandoffPolicy:
    """Heuristic for carrying a session pin across an agent context compaction."""

    enabled: bool = True
    max_age_seconds: float = 60.0
    min_context_ratio: float = 0.80
    min_completion_tokens: int = 5_000
    min_prompt_reduction_ratio: float = 0.50


class BalanceConfigurationError(ValueError):
    """A route's declared balancing policy is malformed or unsafe."""


class NoEligibleBalanceTarget(RuntimeError):
    """No configured balance target can currently serve the request."""


@dataclass(frozen=True)
class BalanceTarget:
    """One configured balance target, ordered by priority then declaration."""

    route_name: str
    priority: int
    order: int


@dataclass(frozen=True)
class BalanceSelection:
    """An immutable selection and all leaf alternatives in selection order."""

    requested_route: Route
    selected_route: Route
    selected_model: str
    reroute_chain: tuple[str, ...]
    alternatives: tuple[RerouteResolution, ...]
    # A balancing reroute normally uses the target route entirely. This
    # immutable subset is the explicit exception: named source-route
    # overrides are re-applied to the selected target's upstream payload.
    preserved_overrides: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    # Explicit source-route filter modules to carry into the selected target.
    preserved_filters: Mapping[str, Mapping[str, Any]] = field(default_factory=lambda: MappingProxyType({}))
    selected_via_selector_fallback: bool = False

    @property
    def balanced(self) -> bool:
        return len(self.reroute_chain) > 1

    def compile_attempt_routes(
        self,
        *,
        routes_by_name: Mapping[str, Route],
        defaults: DefaultSettings | None = None,
    ) -> tuple[tuple[Route, str], ...]:
        """Build retry order: all balance leaves, then selector fallback.

        Each selected leaf retains its own fallback entries. The balancing
        selector's fallback is appended only after every configured balance
        alternative has been exhausted.  Deduplication prevents graph paths or
        fallback declarations from retrying the same concrete route/model pair.
        """
        attempts: list[tuple[Route, str]] = []
        seen: set[tuple[str, str]] = set()

        def append_chain(route: Route, model: str) -> None:
            for candidate_route, candidate_model in resolve_fallback_chain(
                route,
                model,
                routes_by_name=dict(routes_by_name),
                defaults=defaults,
            ):
                if not candidate_model:
                    continue
                identity = (candidate_route.name, candidate_model)
                if identity not in seen:
                    seen.add(identity)
                    attempts.append((candidate_route, candidate_model))

        if self.selected_via_selector_fallback:
            append_chain(self.selected_route, self.selected_model)
        else:
            for leaf in self.alternatives:
                append_chain(leaf.route, leaf.route.model or "")

        selector_chain = resolve_fallback_chain(
            self.requested_route,
            self.requested_route.model or "",
            routes_by_name=dict(routes_by_name),
            defaults=defaults,
        )
        for fallback_route, fallback_model in selector_chain[1:]:
            if not fallback_model:
                continue
            identity = (fallback_route.name, fallback_model)
            if identity not in seen:
                seen.add(identity)
                attempts.append((fallback_route, fallback_model))

        return tuple(attempts)


def parse_balance_targets(value: Any, *, route_name: str) -> tuple[BalanceTarget, ...]:
    """Normalize the public ``balance: {targets: [...]}`` configuration."""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': expected a mapping")
    unknown = set(value) - {
        "targets",
        "preserve_overrides",
        "preserve_filters",
        "affinity_ttl_seconds",
        "idle_reservation",
        "enforce_capabilities",
        "selection_retry_interval_ms",
        "allow_reservation_bypass",
        "promote_when_beneficial",
        "compaction_handoff",
    }
    if unknown:
        rendered = ", ".join(sorted(map(str, unknown)))
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': unsupported key(s) {rendered}")
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': 'targets' must be a list")
    if not raw_targets:
        # An explicit empty target list is the inheritance-safe way to clear a
        # parent balance policy. It is not an executable selector.
        return ()

    result: list[BalanceTarget] = []
    seen: set[str] = set()
    for order, raw_target in enumerate(raw_targets):
        if isinstance(raw_target, str):
            target_name = raw_target.strip()
            priority = 0
        elif isinstance(raw_target, Mapping):
            target_name = raw_target.get("route", "")
            priority = raw_target.get("priority", 0)
            unknown_target = set(raw_target) - {"route", "priority"}
            if unknown_target:
                rendered = ", ".join(sorted(map(str, unknown_target)))
                raise BalanceConfigurationError(
                    f"invalid balance target {order} for route '{route_name}': unsupported key(s) {rendered}"
                )
            if not isinstance(target_name, str):
                target_name = ""
        else:
            raise BalanceConfigurationError(
                f"invalid balance target {order} for route '{route_name}': expected a route name or mapping"
            )

        target_name = target_name.strip()
        if not target_name:
            raise BalanceConfigurationError(
                f"invalid balance target {order} for route '{route_name}': route must be a non-empty string"
            )
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise BalanceConfigurationError(
                f"invalid balance target '{target_name}' for route '{route_name}': priority must be an integer"
            )
        if target_name in seen:
            raise BalanceConfigurationError(f"duplicate balance target '{target_name}' for route '{route_name}'")
        seen.add(target_name)
        result.append(BalanceTarget(target_name, priority, order))

    return tuple(sorted(result, key=lambda target: (-target.priority, target.order)))


def parse_capability_enforcement(value: Any, *, route_name: str) -> bool:
    """Return whether declared capabilities are hard balance constraints.

    Capability declarations are informative by default: an operator may not
    have populated every target yet. Context-fit remains a hard safety check in
    either mode. Enabling this option turns declarations into an explicit
    admission contract for the selector.
    """
    if value is None:
        return False
    if not isinstance(value, Mapping):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': expected a mapping")
    enabled = value.get("enforce_capabilities", False)
    if not isinstance(enabled, bool):
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'enforce_capabilities' must be boolean"
        )
    return enabled


def parse_balance_wait_policy(value: Any, *, route_name: str) -> BalanceWaitPolicy:
    """Validate selector retry/bypass behavior for a positive queue budget."""
    if value is None:
        return BalanceWaitPolicy()
    if not isinstance(value, Mapping):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': expected a mapping")
    raw_interval = value.get("selection_retry_interval_ms", 250)
    if isinstance(raw_interval, bool) or not isinstance(raw_interval, (int, float)) or raw_interval <= 0:
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'selection_retry_interval_ms' must be positive"
        )
    allow_bypass = value.get("allow_reservation_bypass", True)
    if not isinstance(allow_bypass, bool):
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'allow_reservation_bypass' must be boolean"
        )
    return BalanceWaitPolicy(
        retry_interval_seconds=float(raw_interval) / 1000.0,
        allow_reservation_bypass=allow_bypass,
    )


def parse_compaction_handoff_policy(value: Any, *, route_name: str) -> CompactionHandoffPolicy:
    """Validate tunable, default-on compaction handoff heuristics."""
    if value is None:
        return CompactionHandoffPolicy()
    if not isinstance(value, Mapping):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': expected a mapping")
    raw_policy = value.get("compaction_handoff")
    if raw_policy is None:
        return CompactionHandoffPolicy()
    if not isinstance(raw_policy, Mapping):
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'compaction_handoff' must be a mapping"
        )
    unknown = set(raw_policy) - {
        "enabled",
        "max_age_seconds",
        "min_context_ratio",
        "min_completion_tokens",
        "min_prompt_reduction_ratio",
    }
    if unknown:
        rendered = ", ".join(sorted(map(str, unknown)))
        raise BalanceConfigurationError(
            f"invalid compaction_handoff for route '{route_name}': unsupported key(s) {rendered}"
        )
    enabled = raw_policy.get("enabled", True)
    if not isinstance(enabled, bool):
        raise BalanceConfigurationError(
            f"invalid compaction_handoff for route '{route_name}': 'enabled' must be boolean"
        )

    def number(name: str, default: float, *, minimum: float, maximum: float | None = None) -> float:
        item = raw_policy.get(name, default)
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise BalanceConfigurationError(
                f"invalid compaction_handoff for route '{route_name}': '{name}' must be numeric"
            )
        parsed = float(item)
        if parsed < minimum or (maximum is not None and parsed > maximum):
            bounds = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
            raise BalanceConfigurationError(
                f"invalid compaction_handoff for route '{route_name}': '{name}' must be {bounds}"
            )
        return parsed

    minimum_completion = raw_policy.get("min_completion_tokens", 5_000)
    if isinstance(minimum_completion, bool) or not isinstance(minimum_completion, int) or minimum_completion < 0:
        raise BalanceConfigurationError(
            f"invalid compaction_handoff for route '{route_name}': "
            "'min_completion_tokens' must be a non-negative integer"
        )
    return CompactionHandoffPolicy(
        enabled=enabled,
        max_age_seconds=number("max_age_seconds", 60.0, minimum=0.1),
        min_context_ratio=number("min_context_ratio", 0.80, minimum=0.0, maximum=1.0),
        min_completion_tokens=minimum_completion,
        min_prompt_reduction_ratio=number("min_prompt_reduction_ratio", 0.50, minimum=0.0, maximum=1.0),
    )


def parse_preserved_override_keys(value: Any, *, route_name: str) -> tuple[str, ...]:
    """Validate optional source overrides retained across a balancing reroute.

    The vocabulary comes from ``overrides.ALLOWED_OVERRIDE_KEYS`` so every
    supported route override is automatically available here too.
    """
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': expected a mapping")
    raw_keys = value.get("preserve_overrides")
    if raw_keys is None:
        return ()
    if not isinstance(raw_keys, list):
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'preserve_overrides' must be a list"
        )

    result: list[str] = []
    seen: set[str] = set()
    for raw_key in raw_keys:
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise BalanceConfigurationError(
                f"invalid balance for route '{route_name}': 'preserve_overrides' entries must be non-empty strings"
            )
        key = raw_key.strip()
        if key not in ALLOWED_OVERRIDE_KEYS:
            supported = ", ".join(sorted(ALLOWED_OVERRIDE_KEYS))
            raise BalanceConfigurationError(
                f"invalid balance for route '{route_name}': unsupported preserved "
                f"override '{key}' (supported: {supported})"
            )
        if key in seen:
            raise BalanceConfigurationError(
                f"invalid balance for route '{route_name}': duplicate preserved override '{key}'"
            )
        seen.add(key)
        result.append(key)
    return tuple(result)


def parse_preserved_filter_names(value: Any, *, route_name: str) -> tuple[str, ...] | None:
    """Validate source filters retained across reroute; ``None`` means all."""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': expected a mapping")
    raw_names = value.get("preserve_filters")
    if raw_names is None or raw_names is False:
        return ()
    if raw_names is True or (isinstance(raw_names, str) and raw_names.strip().lower() == "all"):
        # Keep this as a sentinel until the resolved source route is available;
        # this also includes future filter modules without another config edit.
        return None
    if not isinstance(raw_names, list):
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'preserve_filters' must be a list, 'all', true, or false"
        )
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise BalanceConfigurationError(
                f"invalid balance for route '{route_name}': 'preserve_filters' entries must be non-empty strings"
            )
        name = raw_name.strip()
        if name in seen:
            raise BalanceConfigurationError(
                f"invalid balance for route '{route_name}': duplicate preserved filter '{name}'"
            )
        seen.add(name)
        result.append(name)
    return tuple(result)


def parse_reservation_policy(value: Any, *, route_name: str) -> BalanceReservationPolicy:
    """Validate the optional affinity/idle-reservation selector policy."""
    if value is None:
        return BalanceReservationPolicy()
    if not isinstance(value, Mapping):
        raise BalanceConfigurationError(f"invalid balance for route '{route_name}': expected a mapping")
    raw_ttl = value.get("affinity_ttl_seconds", 60 * 60)
    if isinstance(raw_ttl, bool) or not isinstance(raw_ttl, (int, float)) or raw_ttl <= 0:
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'affinity_ttl_seconds' must be positive"
        )
    raw = value.get("idle_reservation", {})
    promote_when_beneficial = value.get("promote_when_beneficial", True)
    if not isinstance(promote_when_beneficial, bool):
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'promote_when_beneficial' must be boolean"
        )
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': 'idle_reservation' must be a mapping"
        )
    unknown = set(raw) - {
        "enabled",
        "mode",
        "timeout_seconds",
        "min_seconds",
        "max_seconds",
        "min_context_ratio",
        "max_context_ratio",
        "min_prompt_tokens",
        "min_cached_prompt_tokens",
    }
    if unknown:
        rendered = ", ".join(sorted(map(str, unknown)))
        raise BalanceConfigurationError(
            f"invalid idle_reservation for route '{route_name}': unsupported key(s) {rendered}"
        )
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise BalanceConfigurationError(f"invalid idle_reservation for route '{route_name}': 'enabled' must be boolean")
    mode = raw.get("mode", "priority")
    if mode not in {"priority", "session"}:
        raise BalanceConfigurationError(
            f"invalid idle_reservation for route '{route_name}': 'mode' must be 'priority' or 'session'"
        )

    def number(name: str, default: float) -> float:
        candidate = raw.get(name, default)
        if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
            raise BalanceConfigurationError(
                f"invalid idle_reservation for route '{route_name}': '{name}' must be numeric"
            )
        return float(candidate)

    def non_negative_integer(name: str) -> int:
        candidate = raw.get(name, 0)
        if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
            raise BalanceConfigurationError(
                f"invalid idle_reservation for route '{route_name}': '{name}' must be a non-negative integer"
            )
        return candidate

    minimum = number("min_seconds", 60.0)
    maximum = number("max_seconds", 15 * 60.0)
    low = number("min_context_ratio", 0.10)
    high = number("max_context_ratio", 0.75)
    min_prompt_tokens = non_negative_integer("min_prompt_tokens")
    min_cached_prompt_tokens = non_negative_integer("min_cached_prompt_tokens")
    timeout_seconds = raw.get("timeout_seconds")
    if timeout_seconds is not None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise BalanceConfigurationError(
                f"invalid idle_reservation for route '{route_name}': 'timeout_seconds' must be positive when provided"
            )
        timeout_seconds = float(timeout_seconds)
    if minimum <= 0 or maximum < minimum:
        raise BalanceConfigurationError(
            f"invalid idle_reservation for route '{route_name}': require 0 < min_seconds <= max_seconds"
        )
    if not (0 <= low <= high <= 1):
        raise BalanceConfigurationError(
            f"invalid idle_reservation for route '{route_name}': context ratios must satisfy 0 <= min <= max <= 1"
        )
    return BalanceReservationPolicy(
        affinity_ttl_seconds=float(raw_ttl),
        enabled=enabled,
        mode=mode,
        timeout_seconds=timeout_seconds,
        min_prompt_tokens=min_prompt_tokens,
        min_cached_prompt_tokens=min_cached_prompt_tokens,
        min_seconds=minimum,
        max_seconds=maximum,
        min_context_ratio=low,
        max_context_ratio=high,
        promote_when_beneficial=promote_when_beneficial,
    )


def estimate_promotion_benefit(
    *,
    prompt_tokens: int | None,
    cached_prompt_tokens: int | None,
    completion_tokens: int | None,
    current_metrics: Mapping[str, Any] | None,
    candidate_metrics: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare measured route performance with cold-prefill migration cost.

    This deliberately uses KRM's prompt estimate and observed cache-hit count,
    not a backend tokenizer or an assumed shared prefix cache. The candidate
    is charged for a cold prefill; the current target receives credit only for
    upstream-reported cached tokens. Missing/insufficient inputs fail closed.
    """
    facts: dict[str, Any] = {"decision": "insufficient_metrics"}
    if not isinstance(prompt_tokens, int) or prompt_tokens <= 0:
        return facts
    if not isinstance(completion_tokens, int) or completion_tokens <= 0:
        facts["decision"] = "completion_budget_unavailable"
        return facts

    def rate(metrics: Mapping[str, Any] | None, key: str) -> float | None:
        value = metrics.get(key) if isinstance(metrics, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = float(value)
        return value if value > 0 else None

    current_pp = rate(current_metrics, "prompt_tps")
    current_decode = rate(current_metrics, "completion_tps")
    candidate_pp = rate(candidate_metrics, "prompt_tps")
    candidate_decode = rate(candidate_metrics, "completion_tps")
    if None in (current_pp, current_decode, candidate_pp, candidate_decode):
        return facts

    cached = max(0, min(prompt_tokens, int(cached_prompt_tokens or 0)))
    current_prefill_seconds = (prompt_tokens - cached) / current_pp
    candidate_prefill_seconds = prompt_tokens / candidate_pp
    current_decode_seconds = completion_tokens / current_decode
    candidate_decode_seconds = completion_tokens / candidate_decode
    current_total = current_prefill_seconds + current_decode_seconds
    candidate_total = candidate_prefill_seconds + candidate_decode_seconds
    facts.update(
        {
            "decision": "promote" if candidate_total < current_total else "stay_pinned",
            "estimated_current_seconds": round(current_total, 3),
            "estimated_candidate_seconds": round(candidate_total, 3),
            "estimated_prefill_cost_seconds": round(
                candidate_prefill_seconds - current_prefill_seconds,
                3,
            ),
            "estimated_decode_savings_seconds": round(
                current_decode_seconds - candidate_decode_seconds,
                3,
            ),
            "prompt_tokens_est": prompt_tokens,
            "cached_prompt_tokens": cached,
            "completion_tokens_budget": completion_tokens,
        }
    )
    return facts


def _preserved_overrides(route: Route, *, route_name: str, keys: Sequence[str] | None = None) -> Mapping[str, Any]:
    """Return declared source values as an immutable mapping."""
    if keys is None:
        keys = parse_preserved_override_keys(route.balance, route_name=route_name)
    values = route.overrides or {}
    return MappingProxyType({key: values[key] for key in keys if key in values})


def _preserved_filters(
    route: Route, *, route_name: str, names: Sequence[str] | None = ()
) -> Mapping[str, Mapping[str, Any]]:
    """Return configured source filters selected by name or the all sentinel."""
    if names == ():
        names = parse_preserved_filter_names(route.balance, route_name=route_name)
    configured = route.filters or {}
    if names is None:
        names = tuple(configured)
    missing = [name for name in names if name not in configured]
    if missing:
        rendered = ", ".join(missing)
        raise BalanceConfigurationError(
            f"invalid balance for route '{route_name}': preserved filter(s) not configured on source route: {rendered}"
        )
    return MappingProxyType({name: MappingProxyType(dict(configured[name])) for name in names})


def _resolved_targets(
    route: Route,
    routes_by_name: Mapping[str, Route],
    defaults: DefaultSettings | None,
) -> tuple[BalanceTarget, ...]:
    resolved = resolve_inherited_route(route, dict(routes_by_name), defaults=defaults)
    return parse_balance_targets(resolved.balance, route_name=resolved.name)


def _expand_leaf_targets(
    *,
    requested_route: Route,
    current_route: Route,
    routes_by_name: Mapping[str, Route],
    defaults: DefaultSettings | None,
    chain: tuple[str, ...],
    max_depth: int,
) -> tuple[RerouteResolution, ...]:
    """Recursively flatten a balance graph into independently resolved leaves."""
    targets = _resolved_targets(current_route, routes_by_name, defaults)
    if not targets:
        if not current_route.model:
            raise BalanceConfigurationError(f"balance target '{current_route.name}' has no resolved model")
        return (
            RerouteResolution(
                requested_route_name=requested_route.name,
                destination_route_name=current_route.name,
                route=current_route,
                chain=chain,
            ),
        )

    leaves: list[RerouteResolution] = []
    for target in targets:
        try:
            reroute = resolve_reroute_destination(
                requested_route=requested_route,
                destination_name=target.route_name,
                routes_by_name=dict(routes_by_name),
                defaults=defaults,
                chain=chain,
                max_depth=max_depth,
            )
        except RouteRerouteError as exc:
            raise BalanceConfigurationError(str(exc)) from exc
        leaves.extend(
            _expand_leaf_targets(
                requested_route=requested_route,
                current_route=reroute.route,
                routes_by_name=routes_by_name,
                defaults=defaults,
                chain=reroute.chain,
                max_depth=max_depth,
            )
        )
    return tuple(leaves)


def resolve_balance_selection(
    route: Route,
    *,
    routes_by_name: Mapping[str, Route],
    defaults: DefaultSettings | None = None,
    is_eligible: Callable[[Route], bool] | None = None,
    preferred_route_name: str | None = None,
    max_depth: int = 8,
) -> BalanceSelection:
    """Select the first eligible leaf route from a validated balance graph.

    The ordering is deterministic (higher configured priority, then YAML
    declaration order). Session affinity, health, admission and cache evidence
    are injected later through ``is_eligible``/scoring rather than hidden in
    route inheritance. A direct route returns itself unchanged.
    """
    resolved_source = resolve_inherited_route(route, dict(routes_by_name), defaults=defaults)
    targets = parse_balance_targets(resolved_source.balance, route_name=resolved_source.name)
    preserved_override_keys = parse_preserved_override_keys(resolved_source.balance, route_name=resolved_source.name)
    preserved_filter_names = parse_preserved_filter_names(resolved_source.balance, route_name=resolved_source.name)
    parse_reservation_policy(resolved_source.balance, route_name=resolved_source.name)
    parse_capability_enforcement(
        resolved_source.balance,
        route_name=resolved_source.name,
    )
    parse_balance_wait_policy(
        resolved_source.balance,
        route_name=resolved_source.name,
    )
    parse_compaction_handoff_policy(resolved_source.balance, route_name=resolved_source.name)
    preserved_overrides = _preserved_overrides(
        resolved_source, route_name=resolved_source.name, keys=preserved_override_keys
    )
    preserved_filters = _preserved_filters(
        resolved_source, route_name=resolved_source.name, names=preserved_filter_names
    )
    if (preserved_override_keys or preserved_filter_names is None or preserved_filter_names) and not targets:
        raise BalanceConfigurationError(
            f"invalid balance for route '{resolved_source.name}': "
            "preserve_overrides/preserve_filters require non-empty 'targets' "
            "(requires non-empty targets)"
        )
    if not targets:
        # Dynamic/passthrough routes intentionally obtain their concrete model
        # from the client match at endpoint resolution time. A direct route is
        # therefore valid without a statically configured model.
        direct = RerouteResolution(
            requested_route_name=resolved_source.name,
            destination_route_name=resolved_source.name,
            route=resolved_source,
            chain=(resolved_source.name,),
        )
        return BalanceSelection(
            requested_route=resolved_source,
            selected_route=resolved_source,
            selected_model=resolved_source.model or "",
            reroute_chain=direct.chain,
            alternatives=(direct,),
            preserved_overrides=preserved_overrides,
            preserved_filters=preserved_filters,
        )

    leaves = _expand_leaf_targets(
        requested_route=resolved_source,
        current_route=resolved_source,
        routes_by_name=routes_by_name,
        defaults=defaults,
        chain=(resolved_source.name,),
        max_depth=max_depth,
    )
    # Multiple graph paths may converge on one concrete target. Keep the first
    # (highest-priority) path; retrying it twice would be deceptive.
    unique: list[RerouteResolution] = []
    identities: set[tuple[str, str]] = set()
    for leaf in leaves:
        identity = (leaf.route.name, leaf.route.model or "")
        if identity not in identities:
            identities.add(identity)
            unique.append(leaf)

    if preferred_route_name:
        unique.sort(key=lambda leaf: leaf.route.name != preferred_route_name)

    for leaf in unique:
        if is_eligible is None or is_eligible(leaf.route):
            return BalanceSelection(
                requested_route=resolved_source,
                selected_route=leaf.route,
                selected_model=leaf.route.model or "",
                reroute_chain=leaf.chain,
                alternatives=tuple(unique),
                preserved_overrides=preserved_overrides,
                preserved_filters=preserved_filters,
            )

    # A selector fallback is recovery after the balance pool, including the
    # case where every target is currently unavailable. Do not retry those
    # known-ineligible leaves before the configured terminal path.
    selector_fallbacks = resolve_fallback_chain(
        resolved_source,
        resolved_source.model or "",
        routes_by_name=dict(routes_by_name),
        defaults=defaults,
    )
    for fallback_route, fallback_model in selector_fallbacks[1:]:
        if not fallback_model:
            continue
        if is_eligible is not None and not is_eligible(fallback_route):
            continue
        fallback = RerouteResolution(
            requested_route_name=resolved_source.name,
            destination_route_name=fallback_route.name,
            route=fallback_route,
            chain=(resolved_source.name, fallback_route.name),
        )
        return BalanceSelection(
            requested_route=resolved_source,
            selected_route=fallback_route,
            selected_model=fallback_model,
            reroute_chain=fallback.chain,
            alternatives=(fallback,),
            preserved_overrides=preserved_overrides,
            preserved_filters=preserved_filters,
            selected_via_selector_fallback=True,
        )
    raise NoEligibleBalanceTarget(f"no eligible balance target for route '{resolved_source.name}'")


def validate_balance_policies(routes: Sequence[Route]) -> None:
    """Eagerly validate all inherited policies and nested target graphs."""
    routes_by_name = {route.name: route for route in routes}
    for route in routes:
        resolved = resolve_inherited_route(route, routes_by_name)
        targets = parse_balance_targets(resolved.balance, route_name=route.name)
        preserved_override_keys = parse_preserved_override_keys(resolved.balance, route_name=route.name)
        preserved_filter_names = parse_preserved_filter_names(resolved.balance, route_name=route.name)
        _preserved_filters(resolved, route_name=route.name, names=preserved_filter_names)
        parse_reservation_policy(resolved.balance, route_name=route.name)
        parse_capability_enforcement(resolved.balance, route_name=route.name)
        parse_balance_wait_policy(resolved.balance, route_name=route.name)
        parse_compaction_handoff_policy(resolved.balance, route_name=route.name)
        if (preserved_override_keys or preserved_filter_names is None or preserved_filter_names) and not targets:
            raise BalanceConfigurationError(
                f"invalid balance for route '{route.name}': "
                "preserve_overrides/preserve_filters require non-empty 'targets' "
                "(requires non-empty targets)"
            )
        if not targets:
            continue
        # Expanding every leaf proves route existence, private-target exclusion,
        # route cycles and leaf model availability before config becomes live.
        _expand_leaf_targets(
            requested_route=resolved,
            current_route=resolved,
            routes_by_name=routes_by_name,
            defaults=None,
            chain=(resolved.name,),
            max_depth=8,
        )


__all__ = [
    "BalanceConfigurationError",
    "NoEligibleBalanceTarget",
    "BalanceTarget",
    "BalanceSelection",
    "parse_balance_targets",
    "parse_capability_enforcement",
    "parse_balance_wait_policy",
    "parse_compaction_handoff_policy",
    "parse_preserved_override_keys",
    "parse_reservation_policy",
    "resolve_balance_selection",
    "validate_balance_policies",
]
