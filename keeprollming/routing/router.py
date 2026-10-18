"""Route resolution and HTTP client management.

Contains all routing logic: pattern matching, route resolution,
inheritance resolution, fallback chains, and settings extraction.
Moved from keeprollming/routing.py as part of modularization.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from ..observability import events_routing as _routing_events
from ..types import DEFAULT_FALLBACK_ROUTE, DefaultSettings, Route, RouteMatch

if TYPE_CHECKING:
    from ..core.config_types import RouteSettings

logger = logging.getLogger(__name__)

# A concrete upstream is treated as one serving slot unless the operator
# deliberately configures a different limit on its route.  This is especially
# important for balance targets: an omitted limit must not make a busy backend
# appear indefinitely available to every selector.
DEFAULT_MAX_CONCURRENT = 1


class RouteRerouteError(ValueError):
    """A configured runtime route-to-route delegation is invalid.

    ``extends`` errors are configuration-composition errors.  This exception is
    deliberately separate because a reroute resolves the destination route as
    an independent execution target and must never silently fall back to the
    source route's transport settings.
    """


@dataclass(frozen=True)
class RerouteResolution:
    """Immutable result of resolving one runtime route delegation."""

    requested_route_name: str
    destination_route_name: str
    route: Route
    chain: tuple[str, ...]


# ── Routing functions ────────────────────────────────────────────


def _parse_pattern(pattern: str) -> Tuple[re.Pattern[str], bool]:
    """
    Parse a route pattern into a compiled regex and check if it's wildcard-based.

    Args:
        pattern: Pattern string (e.g., "pass/*", "local/quick", "v1/(?!(v1|pass)/)(.+)")

    Returns:
        Tuple of (compiled_regex, is_wildcard)
    """
    # Check for wildcard patterns like "pass/*" or "code/*"
    if "*" in pattern:
        # Convert glob-style wildcard to regex
        regex_pattern = pattern.replace("*", "(.*)")
        compiled = re.compile(f"^{regex_pattern}$")
        return compiled, True

    # For multiple patterns separated by |, create alternation
    # Only treat | as alternation if it's at the top level (not inside parentheses)
    if "|" in pattern and "(" not in pattern:
        parts = [re.escape(p.strip()) for p in pattern.split("|")]
        regex_pattern = f"^({'|'.join(parts)})$"
        compiled = re.compile(regex_pattern)
        return compiled, False

    # Check if pattern already contains advanced regex syntax (groups, lookahead, etc.)
    has_advanced_regex = any(c in pattern for c in "()[]{}?+*|^$")

    if has_advanced_regex:
        # Assume it's already a valid regex pattern
        try:
            compiled = re.compile(f"^{pattern}$")
            return compiled, False
        except re.error:
            # If invalid regex, fall back to escaped literal match
            escaped = re.escape(pattern)
            compiled = re.compile(f"^{escaped}$")
            return compiled, False

    # Exact match - escape special characters
    escaped = re.escape(pattern)
    compiled = re.compile(f"^{escaped}$")
    return compiled, False


def _extract_model(route: Route, matched_model: str) -> Tuple[str, Dict[str, str]]:
    """
    Extract the actual model from a matched pattern.

    Args:
        route: The matched route
        matched_model: The original client-facing model name

    Returns:
        Tuple of (model, capture_groups)
    """
    # If route has a model defined (not None), use it as the backend
    if route.model is not None and route.model:
        return route.model, {}

    # No extraction pattern - use matched model as-is
    if not route.model_pattern or route.model_pattern is None:
        return matched_model, {}

    # Extract capture groups from regex match
    pattern_regex, is_wildcard = _parse_pattern(route.pattern)
    match = pattern_regex.match(matched_model)

    if not match:
        return matched_model, {}

    # Handle various capture group extraction formats
    backend = _apply_capture_pattern(matched_model, match, route.model_pattern)
    return backend, dict(match.groupdict())


def _apply_capture_pattern(original: str, match: re.Match, pattern: str) -> str:
    """
    Apply a capture group pattern to extract/transform the backend model.

    Supports multiple formats:
    - ${1}, ${2}, ... : Positional groups
    - ${group_name}   : Named groups
    - $1, $2, ...     : Shorthand positional
    - $group_name     : Shorthand named group
    - ${1}/suffix     : Group with suffix
    - prefix_${1}     : Prefix with group

    Args:
        original: The original matched model string
        match: The regex match object
        pattern: The extraction pattern (e.g., "${1}", "$1", "${api_version}/route")

    Returns:
        Transformed backend model name
    """

    # Handle positional groups ${1}, ${2}, etc. FIRST (before named groups)
    def replace_pos_group(match_obj):
        group_num = int(match_obj.group(1)) - 1  # Convert to 0-indexed
        try:
            return match.group(group_num + 1)  # regex groups are 1-indexed
        except IndexError:
            return ""

    result = re.sub(r"\$\{(\d+)\}", replace_pos_group, pattern)

    # Handle $1, $2, etc. shorthand (convert to ${1} format first)
    def replace_shorthand_pos(match_obj):
        group_num = int(match_obj.group(1)) - 1
        try:
            return match.group(group_num + 1)
        except IndexError:
            return ""

    result = re.sub(r"\$(\d+)(?!\{)", lambda m: match.group(int(m.group(1))), result)

    # Handle ${group} or $group format with named groups (after positional)
    def replace_named_group(match_obj):
        group_name = match_obj.group(1)
        try:
            return match.group(group_name)
        except (IndexError, KeyError):
            return ""

    # Replace ${name} patterns
    result = re.sub(r"\$\{([a-zA-Z_]\w*)\}", replace_named_group, result)

    # Replace $name shorthand patterns
    result = re.sub(r"\$([a-zA-Z_]\w*)(?!\d|\{)", lambda m: match.group(m.group(1)), result)

    return result


def _match_route(client_model: str, routes: List[Route]) -> Optional[RouteMatch]:
    """
    Find the first matching route for a client-facing model name.

    Args:
        client_model: The model name from the client request
        routes: List of routes to try (user-defined first, then built-in)

    Returns:
        RouteMatch if found, None otherwise
    """
    for route in routes:
        pattern_regex, _ = _parse_pattern(route.pattern)
        match = pattern_regex.match(client_model)

        if match:
            model, capture_groups = _extract_model(route, client_model)
            return RouteMatch(
                route=route,
                model=model,
                capture_groups=capture_groups,
            )

    return None


def resolve_route_from_routes(
    client_model: str,
    user_routes: Optional[List[Route]] = None,
    req_id: Optional[str] = None,
) -> Tuple[Route, str]:
    """
    Resolve a client-facing model name to the appropriate route and backend model.

    This function implements first-match-wins routing with fallback chain support.
    It also handles route composition (extends) where routes can inherit from other routes.

    Args:
        client_model: The model name from the client request (e.g., "local/quick", "pass/openai/gpt-4")
        user_routes: Optional list of user-defined routes (from config.yaml)
        req_id: Optional request ID for observability event emission.

    Returns:
        Tuple of (matched_route, model_name)
        - route is always a valid Route (fallback if no match found)
        - model is the actual model to use for routing
    """
    # Emit routing.resolution.started
    if req_id:
        _routing_events.emit_started(req_id=req_id, client_model=client_model)

    # Combine user routes and built-in routes
    all_routes = []

    if user_routes:
        all_routes.extend(user_routes)

    # Build a dictionary of routes by name for inheritance resolution
    routes_by_name = {route.name: route for route in all_routes}

    # Try to match against user-defined routes
    route_match = _match_route(client_model, all_routes)

    if route_match:
        matched_route = route_match.route

        # Resolve inheritance - merge with parent settings
        resolved_route = resolve_inherited_route(matched_route, routes_by_name)

        extracted_model, _ = _extract_model(resolved_route, client_model)

        # Emit routing.resolution.resolved
        if req_id:
            _routing_events.emit_resolved(
                req_id=req_id,
                client_model=client_model,
                resolved_route=resolved_route.name,
                model=extracted_model,
                upstream_model=resolved_route.model or "",
            )

        return resolved_route, extracted_model

    # No match found — use default fallback, apply root config defaults
    if not getattr(DEFAULT_FALLBACK_ROUTE, "_route_hierarchy", []):
        from dataclasses import replace

        from ..config import CONFIG, UPSTREAM_BASE_URL

        fallback = DEFAULT_FALLBACK_ROUTE
        # Apply root-level upstream_url if available
        root_upstream = UPSTREAM_BASE_URL or CONFIG.get("upstream_url", "")
        if root_upstream and (not fallback.upstream_url or fallback.upstream_url is None):
            fallback = replace(fallback, upstream_url=root_upstream)
        resolved = replace(fallback, _route_hierarchy=[DEFAULT_FALLBACK_ROUTE.name])

        # Emit routing.resolution.resolved (fallback path)
        if req_id:
            _routing_events.emit_resolved(
                req_id=req_id,
                client_model=client_model,
                resolved_route=resolved.name,
                model=client_model,
                upstream_model=resolved.model or "",
            )

        return resolved, client_model  # Pass through the client model name

    fallback = DEFAULT_FALLBACK_ROUTE
    root_upstream = UPSTREAM_BASE_URL or CONFIG.get("upstream_url", "")
    if root_upstream and (not fallback.upstream_url or fallback.upstream_url is None):
        from dataclasses import replace

        fallback = replace(fallback, upstream_url=root_upstream)

    # Emit routing.resolution.resolved (fallback path)
    if req_id:
        _routing_events.emit_resolved(
            req_id=req_id,
            client_model=client_model,
            resolved_route=fallback.name,
            model=client_model,
            upstream_model=fallback.model or "",
        )

    return fallback, client_model


def resolve_inherited_route(
    route: Route,
    routes_by_name: Dict[str, Route],
    visited: set = None,
    defaults: DefaultSettings = None,
    _apply_defaults: bool = True,
) -> Route:
    """
    Resolve a route's full configuration by inheriting from parent routes.

    This handles hierarchical route configuration where routes can extend other routes.
    Child routes override parent settings while inheriting unspecified ones.

    Args:
        route: The route to resolve (may have extends set)
        routes_by_name: Dictionary of all available routes by name
        visited: Set of already-visited route names (to prevent infinite loops)
        defaults: Global default settings for inheritance
        _apply_defaults: Internal flag — only apply defaults on the top-level call.
            Recursive calls for intermediate parents use _apply_defaults=False so that
            None values propagate correctly through the merge loop.

    Returns:
        A fully resolved Route with all inherited settings applied
    """
    if visited is None:
        visited = set()

    # Prevent infinite inheritance loops
    if route.name in visited:
        logger.warning("Circular inheritance detected for route '%s'", route.name)
        return route

    # Handle multiple extends (comma-separated string from config parsing)
    extends_list = []
    if route.extends:
        if isinstance(route.extends, str):
            extends_list = [e.strip() for e in route.extends.split(",")]
        elif isinstance(route.extends, list):
            extends_list = route.extends

    # If no parent, return as-is (defer defaults to the final merge path)
    if not extends_list:
        merged_settings = {
            "name": route.name,
            "pattern": route.pattern,
            "summary_enabled": route.summary_enabled,
            "passthrough_enabled": route.passthrough_enabled,
            "model": route.model,
            "summary_model": route.summary_model,
            "ctx_len": route.ctx_len,
            "max_tokens": route.max_tokens,
            "transform_reasoning_content": route.transform_reasoning_content,
            "add_empty_content_when_reasoning_only": route.add_empty_content_when_reasoning_only,
            "reasoning_placeholder_content": route.reasoning_placeholder_content,
            "model_pattern": route.model_pattern,
            "upstream_url": route.upstream_url,
            "upstream_headers": route.upstream_headers,
            "api_key": route.api_key,
            "api_keys": route.api_keys,
            "client_access": route.client_access,
            "fallback_chain": route.fallback_chain,
            "circuit_breaker_enabled": route.circuit_breaker_enabled,
            "failure_threshold": route.failure_threshold,
            "recovery_timeout": route.recovery_timeout,
            "request_timeout": route.request_timeout,
            "max_concurrent": route.max_concurrent,
            "queue_timeout": route.queue_timeout,
            "routing_priority": route.routing_priority,
            "queue_aging": route.queue_aging,
            "deadline_policy": route.deadline_policy,
            "cost_priority": route.cost_priority,
            "performance_logs_dir": route.performance_logs_dir,
            "capabilities": route.capabilities,
            "balance": route.balance,
            "overrides": route.overrides,
            "filters": route.filters,
        }

        # Initialize route hierarchy for routes without parents (e.g., built-in routes)
        existing_hierarchy = getattr(route, "_route_hierarchy", [])
        if not existing_hierarchy:
            merged_settings["_route_hierarchy"] = [route.name]
        else:
            merged_settings["_route_hierarchy"] = existing_hierarchy

        # Apply defaults only on the top-level call
        if _apply_defaults:

            def apply_default(val, default):
                return val if val is not None else default

            merged_settings["summary_enabled"] = apply_default(merged_settings["summary_enabled"], True)
            merged_settings["passthrough_enabled"] = apply_default(merged_settings["passthrough_enabled"], False)
            merged_settings["transform_reasoning_content"] = apply_default(
                merged_settings["transform_reasoning_content"], False
            )
            merged_settings["add_empty_content_when_reasoning_only"] = apply_default(
                merged_settings["add_empty_content_when_reasoning_only"], False
            )
            merged_settings["reasoning_placeholder_content"] = apply_default(
                merged_settings["reasoning_placeholder_content"], ""
            )
            merged_settings["upstream_url"] = apply_default(merged_settings["upstream_url"], None)
            merged_settings["upstream_headers"] = apply_default(merged_settings["upstream_headers"], {})
            merged_settings["api_key"] = apply_default(merged_settings["api_key"], None)
            merged_settings["api_keys"] = apply_default(
                merged_settings["api_keys"],
                list(defaults.api_keys) if defaults else None,
            )
            merged_settings["client_access"] = apply_default(
                merged_settings["client_access"],
                defaults.client_access if defaults else None,
            )
            merged_settings["fallback_chain"] = apply_default(merged_settings["fallback_chain"], [])
            merged_settings["circuit_breaker_enabled"] = apply_default(
                merged_settings["circuit_breaker_enabled"], False
            )
            merged_settings["failure_threshold"] = apply_default(merged_settings["failure_threshold"], 3)
            merged_settings["recovery_timeout"] = apply_default(merged_settings["recovery_timeout"], 60)
            merged_settings["request_timeout"] = apply_default(merged_settings["request_timeout"], None)
            merged_settings["max_concurrent"] = apply_default(
                merged_settings["max_concurrent"],
                DEFAULT_MAX_CONCURRENT,
            )
            merged_settings["queue_timeout"] = apply_default(merged_settings["queue_timeout"], None)
            merged_settings["routing_priority"] = apply_default(merged_settings["routing_priority"], 0)
            merged_settings["queue_aging"] = apply_default(merged_settings["queue_aging"], None)
            merged_settings["deadline_policy"] = apply_default(merged_settings["deadline_policy"], None)
            merged_settings["cost_priority"] = apply_default(merged_settings["cost_priority"], 999)

        route_with_hierarchy = Route(**{**route.__dict__, **merged_settings, "_route_hierarchy": [route.name]})

        return route_with_hierarchy

    visited.add(route.name)

    # Start with child's own settings (as a base to override from parents)
    merged_settings = {
        "name": route.name,
        "pattern": route.pattern,
        "summary_enabled": route.summary_enabled,
        "passthrough_enabled": route.passthrough_enabled,
        "model": route.model,
        "summary_model": route.summary_model,
        "ctx_len": route.ctx_len,
        "max_tokens": route.max_tokens,
        "transform_reasoning_content": route.transform_reasoning_content,
        "add_empty_content_when_reasoning_only": route.add_empty_content_when_reasoning_only,
        "reasoning_placeholder_content": route.reasoning_placeholder_content,
        "model_pattern": route.model_pattern,
        "upstream_url": route.upstream_url,
        "upstream_headers": route.upstream_headers,
        "api_key": route.api_key,
        "api_keys": route.api_keys,
        "client_access": route.client_access,
        "fallback_chain": route.fallback_chain,
        "circuit_breaker_enabled": route.circuit_breaker_enabled,
        "failure_threshold": route.failure_threshold,
        "recovery_timeout": route.recovery_timeout,
        "request_timeout": route.request_timeout,
        "max_concurrent": route.max_concurrent,
        "queue_timeout": route.queue_timeout,
        "routing_priority": route.routing_priority,
        "queue_aging": route.queue_aging,
        "deadline_policy": route.deadline_policy,
        "cost_priority": route.cost_priority,
        "performance_logs_dir": route.performance_logs_dir,
        "capabilities": route.capabilities,
        "balance": route.balance,
        "overrides": route.overrides,
        "filters": route.filters,
    }

    # Merge settings from each parent in order (left to right).
    # The original route's own values are captured before the loop so we can
    # distinguish "explicit child value" from "accumulated parent value".
    child_own_values = dict(merged_settings)

    for parent_name in extends_list:
        # Track if we resolved any parents for hierarchy building
        last_resolved_parent = None

        parent = routes_by_name.get(parent_name)
        if not parent:
            logger.warning("Parent route '%s' not found for route '%s'", parent_name, route.name)
            continue

        # Recursively resolve parent's inheritance first (defer defaults to top-level)
        resolved_parent = resolve_inherited_route(parent, routes_by_name, visited.copy(), _apply_defaults=False)
        last_resolved_parent = resolved_parent

        # Merge this parent's settings into merged_settings
        new_merged = {}
        for key in [
            "summary_enabled",
            "passthrough_enabled",
            "model",
            "summary_model",
            "ctx_len",
            "max_tokens",
            "transform_reasoning_content",
            "add_empty_content_when_reasoning_only",
            "reasoning_placeholder_content",
            "model_pattern",
            "upstream_url",
            "upstream_headers",
            "api_key",
            "api_keys",
            "client_access",
            "fallback_chain",
            "circuit_breaker_enabled",
            "failure_threshold",
            "recovery_timeout",
            "request_timeout",
            "max_concurrent",
            "queue_timeout",
            "routing_priority",
            "queue_aging",
            "deadline_policy",
            "cost_priority",
            "performance_logs_dir",
            "capabilities",
            "balance",
        ]:
            own_val = child_own_values[key]  # Child's explicit value (captured before loop)
            parent_val = getattr(resolved_parent, key, None)
            if own_val is not None:
                new_merged[key] = own_val  # Keep child's explicit value — always wins
            elif parent_val is not None:
                new_merged[key] = parent_val  # Parent has a real value — inherit it

        # Special merge for overrides dict: child keys override parent keys
        child_overrides = merged_settings.get("overrides", {})
        parent_overrides = getattr(resolved_parent, "overrides", {})
        if parent_overrides or child_overrides:
            merged_overrides = {**parent_overrides, **child_overrides}
            new_merged["overrides"] = merged_overrides

        # Filters are a map of independent modules, so compose them across
        # multiple parents instead of letting a later parent's partial filter
        # map discard modules inherited from an earlier parent. Later parents
        # override the same filter name; the child's explicitly configured
        # filters remain highest priority.
        inherited_filters = merged_settings.get("filters") or {}
        parent_filters = getattr(resolved_parent, "filters", None) or {}
        child_filters = child_own_values["filters"] or {}
        if inherited_filters or parent_filters or child_filters:
            new_merged["filters"] = {**inherited_filters, **parent_filters, **child_filters}

        merged_settings.update(new_merged)

    # Build route hierarchy path: parent -> ... -> child
    # Start with last resolved parent's hierarchy, then add current route name
    parent_hierarchy = getattr(last_resolved_parent, "_route_hierarchy", []) if last_resolved_parent else []
    merged_settings["_route_hierarchy"] = parent_hierarchy + [route.name]

    # Apply defaults only on the top-level call
    if _apply_defaults:

        def apply_default(val, default):
            return val if val is not None else default

        merged_settings["summary_enabled"] = apply_default(merged_settings["summary_enabled"], True)
        merged_settings["passthrough_enabled"] = apply_default(merged_settings["passthrough_enabled"], False)
        merged_settings["transform_reasoning_content"] = apply_default(
            merged_settings["transform_reasoning_content"], False
        )
        merged_settings["add_empty_content_when_reasoning_only"] = apply_default(
            merged_settings["add_empty_content_when_reasoning_only"], False
        )
        merged_settings["reasoning_placeholder_content"] = apply_default(
            merged_settings["reasoning_placeholder_content"], ""
        )
        merged_settings["upstream_url"] = apply_default(merged_settings["upstream_url"], None)  # No default upstream
        merged_settings["upstream_headers"] = apply_default(merged_settings["upstream_headers"], {})
        merged_settings["api_key"] = apply_default(merged_settings["api_key"], None)
        merged_settings["api_keys"] = apply_default(
            merged_settings["api_keys"],
            list(defaults.api_keys) if defaults else None,
        )
        merged_settings["client_access"] = apply_default(
            merged_settings["client_access"],
            defaults.client_access if defaults else None,
        )
        merged_settings["fallback_chain"] = apply_default(merged_settings["fallback_chain"], [])
        merged_settings["circuit_breaker_enabled"] = apply_default(merged_settings["circuit_breaker_enabled"], False)
        merged_settings["failure_threshold"] = apply_default(merged_settings["failure_threshold"], 3)
        merged_settings["recovery_timeout"] = apply_default(merged_settings["recovery_timeout"], 60)
        # Keep the timeout unresolved when no defaults object was supplied.
        # The application-level RouteSettings resolver then applies the live
        # root default.  Materializing 120 here would mask
        # ``default_request_timeout`` for every inherited route.
        merged_settings["request_timeout"] = apply_default(
            merged_settings["request_timeout"],
            defaults.request_timeout if defaults else None,
        )
        merged_settings["max_concurrent"] = apply_default(
            merged_settings["max_concurrent"],
            DEFAULT_MAX_CONCURRENT,
        )
        merged_settings["queue_timeout"] = apply_default(merged_settings["queue_timeout"], None)
        merged_settings["routing_priority"] = apply_default(merged_settings["routing_priority"], 0)
        merged_settings["queue_aging"] = apply_default(merged_settings["queue_aging"], None)
        merged_settings["deadline_policy"] = apply_default(merged_settings["deadline_policy"], None)
        merged_settings["cost_priority"] = apply_default(merged_settings["cost_priority"], 999)
        merged_settings["performance_logs_dir"] = apply_default(
            merged_settings["performance_logs_dir"],
            defaults.performance_logs_dir if defaults else "__performance_logs",
        )

    return Route(**merged_settings)


def resolve_reroute_destination(
    *,
    requested_route: Route,
    destination_name: str,
    routes_by_name: Dict[str, Route],
    defaults: DefaultSettings | None = None,
    chain: tuple[str, ...] = (),
    max_depth: int = 8,
) -> RerouteResolution:
    """Resolve a runtime route delegation without inheriting source settings.

    A route configured as a reroute destination is resolved from its own
    ``extends`` hierarchy.  In particular, the source route's URL, model,
    filters, API credentials, admission settings and fallback chain are never
    merged into it.  This is the primitive that balancing and explicit route
    delegation use; it does not itself select a destination or mutate runtime
    state.
    """
    name = destination_name.strip() if isinstance(destination_name, str) else ""
    if not name:
        raise RouteRerouteError("reroute destination must be a non-empty route name")
    if max_depth < 1:
        raise RouteRerouteError("reroute max_depth must be positive")

    effective_chain = chain or (requested_route.name,)
    if len(effective_chain) >= max_depth:
        raise RouteRerouteError(f"reroute depth exceeds {max_depth}: {' -> '.join((*effective_chain, name))}")
    if name in effective_chain:
        raise RouteRerouteError(f"reroute cycle detected: {' -> '.join((*effective_chain, name))}")

    destination = routes_by_name.get(name)
    if destination is None:
        raise RouteRerouteError(f"reroute destination '{name}' not found for route '{requested_route.name}'")
    if getattr(destination, "_is_private", False):
        raise RouteRerouteError(f"reroute destination '{name}' is private and cannot be targeted")

    resolved = resolve_inherited_route(destination, routes_by_name, defaults=defaults)
    return RerouteResolution(
        requested_route_name=requested_route.name,
        destination_route_name=name,
        route=resolved,
        chain=(*effective_chain, name),
    )


def resolve_fallback_chain(
    primary_route: Route,
    primary_backend: str,
    client_request_id: Optional[str] = None,
    *,
    routes_by_name: Optional[Dict[str, Route]] = None,
    defaults: Optional[DefaultSettings] = None,
) -> List[Tuple[Route, str]]:
    """
    Resolve a fallback chain for automatic rerouting when backend is unavailable.

    This function returns the complete list of (route, model) pairs to try,
    starting with the primary route and following the fallback chain.

    Args:
        primary_route: The originally matched route
        primary_backend: The primary backend model name
        client_request_id: Optional request ID for tracking/debugging

    Returns:
        List of (route, model) tuples in order to try
        Each tuple represents a routing attempt
    """
    attempts = [(primary_route, primary_backend)]
    visited_attempts = {(primary_route.name, primary_backend)}

    if not primary_route.fallback_chain:
        return attempts

    for fallback_option in primary_route.fallback_chain:
        # Check max depth (default 3) - count only actual fallback attempts
        if len(attempts) - 1 >= 3:  # -1 because we don't count the primary
            break

        # Handle different fallback option formats
        if isinstance(fallback_option, str):
            # A configured route name is the normal public contract. Resolve
            # its inheritance so it contributes its own endpoint and model.
            named_route = (routes_by_name or {}).get(fallback_option)
            if named_route is not None:
                resolved_route = resolve_inherited_route(
                    named_route,
                    routes_by_name or {},
                    defaults=defaults,
                )
                fallback_model = resolved_route.model
                if not fallback_model:
                    continue
                identity = (resolved_route.name, fallback_model)
                if identity not in visited_attempts:
                    attempts.append((resolved_route, fallback_model))
                    visited_attempts.add(identity)
                continue

            # Retain direct-model fallback support for existing configurations.
            identity = (primary_route.name, fallback_option)
            if identity not in visited_attempts:
                attempts.append((primary_route, fallback_option))
                visited_attempts.add(identity)

        elif isinstance(fallback_option, dict):
            # Complex option with conditions or metadata
            target = fallback_option.get("model")
            fallback_option.get("condition", "always")

            if not target:
                continue

            # For now, always try (condition evaluation can be added later)
            identity = (primary_route.name, target)
            if identity not in visited_attempts:
                attempts.append((primary_route, target))
                visited_attempts.add(identity)

    return attempts


def get_route_settings(
    route: Route,
    model: str,
    *,
    defaults: DefaultSettings | None = None,
) -> "RouteSettings":
    """
    Extract all settings from a matched route as a typed RouteSettings object.

    Args:
        route: The matched route (should already be resolved with inheritance applied)
        model: The resolved model name

    Returns:
        RouteSettings object with all defaults applied when ``defaults`` is
        supplied.  Callers outside the loaded application configuration may
        omit it and retain the standalone defaults used by ``from_route``.
    """
    from ..core.config_types import RouteSettings

    if defaults is not None:
        return RouteSettings.resolve(route, defaults, model)
    return RouteSettings.from_route(route, model)


__all__ = [
    "_parse_pattern",
    "_extract_model",
    "_apply_capture_pattern",
    "_match_route",
    "resolve_route_from_routes",
    "resolve_inherited_route",
    "RouteRerouteError",
    "RerouteResolution",
    "resolve_reroute_destination",
    "resolve_fallback_chain",
    "get_route_settings",
]
