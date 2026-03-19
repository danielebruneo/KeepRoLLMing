from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Union

# Sentinel value to detect unset fields in inheritance
_UNSET = object()


@dataclass(frozen=True)
class DefaultSettings:
    """Default settings at root level of config - applied when not overridden."""
    ctx_len: int = 8192
    max_tokens: int = 4096
    summary_enabled: bool = True
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""


@dataclass(frozen=True)
class ModelConfig:
    """Configuration specific to a model - can override defaults."""
    ctx_len: int = _UNSET  # type: ignore
    max_tokens: int = _UNSET  # type: ignore
    summary_enabled: bool = _UNSET  # type: ignore


@dataclass(frozen=True)
class Route:
    """Represents a routing rule with model settings and fallback chain."""
    name: str
    pattern: str  # Pattern to match (e.g., "local/quick", "pass/*")

    # Core settings - use sentinel for inheritance detection, but provide defaults
    summary_enabled: bool = True
    passthrough_enabled: bool = False

    # Model configuration - can reference models dict or specify directly
    main_model: Optional[str] = None
    summary_model: Optional[str] = None
    
    # Settings that can be overridden at route level (will fall back to model config)
    ctx_len: int = _UNSET  # type: ignore
    max_tokens: int = _UNSET  # type: ignore

    # Reasoning content handling - use sentinel for inheritance detection, but provide defaults
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""

    # Backend configuration (for passthrough) - use sentinel for inheritance detection, but provide defaults
    backend_model_pattern: Optional[str] = None

    # Upstream configuration - allows different upstreams per route - use sentinel
    upstream_url: Optional[str] = None
    upstream_headers: Dict[str, str] = field(default_factory=dict)  # Custom headers for this route

    # Fallback chain for automatic rerouting - use sentinel
    fallback_chain: List[Union[str, Dict[str, Any]]] = field(default_factory=list)

    # Circuit breaker settings (optional) - use sentinel
    circuit_breaker_enabled: bool = False
    failure_threshold: int = 3
    recovery_timeout: int = 60

    # Cost priority for fallbacks (lower = higher priority) - use sentinel
    cost_priority: int = 999

    # Route composition - extend another route and override settings
    extends: Optional[str] = None


@dataclass(frozen=True)
class RouteMatch:
    """Result of route matching - contains matched route and extracted backend model."""
    route: Route
    backend_model: str  # The actual model to use (extracted from pattern if needed)
    capture_groups: Dict[str, str] = field(default_factory=dict)


# Built-in default routes that work without any configuration
BUILTIN_ROUTES: List[Route] = [
    # Quick profile - fast responses with summarization
    Route(
        name="quick-default",
        pattern="local/quick|quick",
        main_model="qwen2.5-3b-instruct",
        summary_model="qwen2.5-1.5b-instruct",
        ctx_len=8192,
        max_tokens=4096,
        summary_enabled=True,
        passthrough_enabled=False,
    ),

    # Main profile - balanced performance
    Route(
        name="main-default",
        pattern="local/main|main",
        main_model="qwen2.5-v1-7b-instruct",
        summary_model="qwen2.5-3b-instruct",
        ctx_len=8192,
        max_tokens=4096,
        summary_enabled=True,
        passthrough_enabled=False,
    ),

    # Deep profile - maximum context and quality
    Route(
        name="deep-default",
        pattern="local/deep|deep",
        main_model="qwen2.5-27b-instruct",
        summary_model="qwen2.5-7b-instruct",
        ctx_len=16384,
        max_tokens=8192,
        summary_enabled=True,
        passthrough_enabled=False,
    ),

    # Code/Senior - specialized for senior developer tasks
    Route(
        name="code-senior-default",
        pattern="code/senior|senior",
        main_model="qwen3.5-35b-a3b",
        summary_model="qwen2.5-7b-instruct",
        ctx_len=16384,
        max_tokens=8192,
        summary_enabled=True,
        passthrough_enabled=False,
    ),

    # Code/Junior - simplified for junior developer tasks
    Route(
        name="code-junior-default",
        pattern="code/junior|junior",
        main_model="qwen2.5-7b-instruct",
        summary_model="qwen2.5-1.5b-instruct",
        ctx_len=8192,
        max_tokens=4096,
        summary_enabled=True,
        passthrough_enabled=False,
    ),

    # Passthrough - bypass summarization, forward directly
    Route(
        name="passthrough-default",
        pattern="pass/*",
        passthrough_enabled=True,
        summary_enabled=False,
        backend_model_pattern="${1}",  # Extract model from pass/(.*)
    ),
]

# Fallback route for unmatched models
DEFAULT_FALLBACK_ROUTE = Route(
    name="fallback-default",
    pattern="*",
    main_model="qwen2.5-v1-7b-instruct",
    summary_model="qwen2.5-3b-instruct",
    ctx_len=8192,
    max_tokens=4096,
    passthrough_enabled=False,
)


def _parse_pattern(pattern: str) -> Tuple[re.Pattern[str], bool]:
    """
    Parse a route pattern into a compiled regex and check if it's wildcard-based.
    
    Args:
        pattern: Pattern string (e.g., "pass/*", "local/quick")
        
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
    if "|" in pattern:
        # Split and escape special regex characters for each part
        parts = [re.escape(p.strip()) for p in pattern.split("|")]
        regex_pattern = f"^({'|'.join(parts)})$"
        compiled = re.compile(regex_pattern)
        return compiled, False
    
    # Exact match - escape special characters
    escaped = re.escape(pattern)
    compiled = re.compile(f"^{escaped}$")
    return compiled, False


def _extract_backend_model(route: Route, matched_model: str) -> Tuple[str, Dict[str, str]]:
    """
    Extract the actual backend model from a matched pattern.

    Args:
        route: The matched route
        matched_model: The original client-facing model name

    Returns:
        Tuple of (backend_model, capture_groups)
    """
    # If route has a main_model defined (not _UNSET), use it as the backend
    if route.main_model is not _UNSET and route.main_model:
        return route.main_model, {}

    # For passthrough routes with pattern extraction
    if not route.backend_model_pattern or route.backend_model_pattern is _UNSET or not route.pattern.startswith("pass/"):
        # No extraction needed - use the matched model as-is
        return matched_model, {}

    # Extract capture groups from regex match
    pattern_regex, is_wildcard = _parse_pattern(route.pattern)
    match = pattern_regex.match(matched_model)

    if not match:
        return matched_model, {}

    # Handle ${1} style capture group extraction
    if route.backend_model_pattern == "${1}" and match.groups():
        backend = match.group(1).strip()
        return backend, {"extracted": match.group(1)}

    return matched_model, {}


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
            backend_model, capture_groups = _extract_backend_model(route, client_model)
            return RouteMatch(
                route=route,
                backend_model=backend_model,
                capture_groups=capture_groups,
            )
    
    return None


def resolve_route(client_model: str, user_routes: Optional[List[Route]] = None) -> Tuple[Optional[Route], str]:
    """
    Resolve a client-facing model name to the appropriate route and backend model.

    This function implements first-match-wins routing with fallback chain support.
    It also handles route composition (extends) where routes can inherit from other routes.

    Args:
        client_model: The model name from the client request (e.g., "local/quick", "pass/openai/gpt-4")
        user_routes: Optional list of user-defined routes (from config.yaml)

    Returns:
        Tuple of (matched_route, backend_model_name)
        - route can be None if no match found (shouldn't happen with fallback)
        - backend_model is the actual model to use for routing
    """
    # Combine user routes and built-in routes
    all_routes = []

    if user_routes:
        all_routes.extend(user_routes)

    all_routes.extend(BUILTIN_ROUTES)

    # Build a dictionary of routes by name for inheritance resolution
    routes_by_name = {route.name: route for route in all_routes}

    # Try to match against routes in order (user-defined first, then built-in)
    route_match = _match_route(client_model, all_routes)

    if route_match:
        matched_route = route_match.route
        
        # Resolve inheritance - merge with parent settings
        resolved_route = resolve_inherited_route(matched_route, routes_by_name)
        
        return resolved_route, route_match.backend_model

    # No match found - use default fallback
    return DEFAULT_FALLBACK_ROUTE, "qwen2.5-v1-7b-instruct"


def resolve_inherited_route(route: Route, routes_by_name: Dict[str, Route], visited: set = None) -> Route:
    """
    Resolve a route's full configuration by inheriting from parent routes.

    This handles hierarchical route configuration where routes can extend other routes.
    Child routes override parent settings while inheriting unspecified ones.

    Args:
        route: The route to resolve (may have extends set)
        routes_by_name: Dictionary of all available routes by name
        visited: Set of already-visited route names (to prevent infinite loops)

    Returns:
        A fully resolved Route with all inherited settings applied
    """
    if visited is None:
        visited = set()

    # Prevent infinite inheritance loops
    if route.name in visited:
        print(f"Warning: Circular inheritance detected for route '{route.name}'")
        return route

    # If no parent, return as-is
    if not route.extends or route.extends not in routes_by_name:
        return route

    visited.add(route.name)

    # Get parent route (resolve its inheritance first)
    parent = routes_by_name.get(route.extends)
    if not parent:
        print(f"Warning: Parent route '{route.extends}' not found for route '{route.name}'")
        return route
    
    # Recursively resolve parent's inheritance first
    resolved_parent = resolve_inherited_route(parent, routes_by_name, visited.copy())

    # Merge settings: child overrides parent (child values take precedence when explicitly set)
    def get_value(child_val, parent_val):
        """Get value from child if set, otherwise inherit from parent."""
        if child_val is _UNSET:
            return parent_val
        return child_val
    
    merged_settings = {
        "name": route.name,
        "pattern": route.pattern,
        "summary_enabled": get_value(route.summary_enabled, resolved_parent.summary_enabled),
        "passthrough_enabled": get_value(route.passthrough_enabled, resolved_parent.passthrough_enabled),
        "main_model": get_value(route.main_model, resolved_parent.main_model),
        "summary_model": get_value(route.summary_model, resolved_parent.summary_model),
        "ctx_len": get_value(route.ctx_len, resolved_parent.ctx_len),
        "max_tokens": get_value(route.max_tokens, resolved_parent.max_tokens),
        "transform_reasoning_content": get_value(route.transform_reasoning_content, resolved_parent.transform_reasoning_content),
        "add_empty_content_when_reasoning_only": get_value(route.add_empty_content_when_reasoning_only, resolved_parent.add_empty_content_when_reasoning_only),
        "reasoning_placeholder_content": get_value(route.reasoning_placeholder_content, resolved_parent.reasoning_placeholder_content),
        "backend_model_pattern": get_value(route.backend_model_pattern, resolved_parent.backend_model_pattern),
        "upstream_url": get_value(route.upstream_url, resolved_parent.upstream_url),
        "upstream_headers": get_value(route.upstream_headers, resolved_parent.upstream_headers),
        "fallback_chain": get_value(route.fallback_chain, resolved_parent.fallback_chain),
        "circuit_breaker_enabled": get_value(route.circuit_breaker_enabled, resolved_parent.circuit_breaker_enabled),
        "failure_threshold": get_value(route.failure_threshold, resolved_parent.failure_threshold),
        "recovery_timeout": get_value(route.recovery_timeout, resolved_parent.recovery_timeout),
        "cost_priority": get_value(route.cost_priority, resolved_parent.cost_priority),
        "extends": None,  # Resolved - no longer needs to extend
    }

    return Route(**merged_settings)


def resolve_fallback_chain(
    primary_route: Route,
    primary_backend: str,
    client_request_id: Optional[str] = None
) -> List[Tuple[Route, str]]:
    """
    Resolve a fallback chain for automatic rerouting when backend is unavailable.

    This function returns the complete list of (route, backend_model) pairs to try,
    starting with the primary route and following the fallback chain.

    Args:
        primary_route: The originally matched route
        primary_backend: The primary backend model name
        client_request_id: Optional request ID for tracking/debugging
        
    Returns:
        List of (route, backend_model) tuples in order to try
        Each tuple represents a routing attempt
    """
    attempts = [(primary_route, primary_backend)]
    visited_models = {primary_backend}  # Track visited models to prevent loops
    
    if not primary_route.fallback_chain:
        return attempts
    
    for fallback_option in primary_route.fallback_chain:
        # Check max depth (default 3) - count only actual fallback attempts
        if len(attempts) - 1 >= 3:  # -1 because we don't count the primary
            break
        
        # Handle different fallback option formats
        if isinstance(fallback_option, str):
            # Simple string - could be route name or model name
            fallback_target = fallback_option
            
            # Check if it's a known route name (built-in or user-defined)
            built_in_match = _match_route(fallback_target, BUILTIN_ROUTES)
            if built_in_match:
                # It's a route reference - use that route's models
                attempts.append((built_in_match.route, fallback_target))
                visited_models.add(fallback_target)
            else:
                # It's a direct model name
                if fallback_target not in visited_models:
                    attempts.append((primary_route, fallback_target))
                    visited_models.add(fallback_target)
        
        elif isinstance(fallback_option, dict):
            # Complex option with conditions or metadata
            target = fallback_option.get("model")
            condition = fallback_option.get("condition", "always")
            
            if not target:
                continue
            
            # For now, always try (condition evaluation can be added later)
            if target not in visited_models:
                built_in_match = _match_route(target, BUILTIN_ROUTES)
                if built_in_match:
                    attempts.append((built_in_match.route, target))
                else:
                    attempts.append((primary_route, target))
                visited_models.add(target)
    
    return attempts


def get_route_settings(route: Route, backend_model: str) -> Dict[str, Any]:
    """
    Extract all settings from a matched route for use in the application.

    Args:
        route: The matched route (should already be resolved with inheritance applied)
        backend_model: The resolved backend model name

    Returns:
        Dictionary of all route settings including upstream configuration
    """
    return {
        "route_name": route.name,
        "backend_model": backend_model,
        "summary_enabled": route.summary_enabled,
        "passthrough_enabled": route.passthrough_enabled,
        "main_model": route.main_model or backend_model,
        "summary_model": route.summary_model,
        "ctx_len": route.ctx_len,
        "max_tokens": route.max_tokens,
        "transform_reasoning_content": route.transform_reasoning_content,
        "add_empty_content_when_reasoning_only": route.add_empty_content_when_reasoning_only,
        "reasoning_placeholder_content": route.reasoning_placeholder_content,
        "fallback_chain": route.fallback_chain,
        # Upstream configuration
        "upstream_url": route.upstream_url,
        "upstream_headers": route.upstream_headers or {},
    }
