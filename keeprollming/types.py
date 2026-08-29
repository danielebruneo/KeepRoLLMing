"""Shared types and constants for the orchestrator.

This module contains dataclasses and constants that are used across multiple modules
(config, routing) to avoid circular import dependencies. By centralizing shared types
here, we can break cycles like: config -> routing -> config
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

# ── Global defaults ──────────────────────────────────────────────────────────
DEFAULT_REQUEST_TIMEOUT: float = 300.0
"""Default timeout for upstream HTTP requests (connect, read, write, pool)."""

# Sentinel value to detect unset fields in inheritance
# Sentinels (to be removed — replaced by proper defaults)
# _UNSET = object()  # DEPRECATED: use None with Optional types


@dataclass(frozen=True)
class DefaultSettings:
    """Default settings at root level of config - applied when not overridden.

    Attributes:
        ctx_len: Default context length (tokens)
        max_tokens: Default maximum completion tokens
        summary_enabled: Whether summarization is enabled by default
        transform_reasoning_content: Whether to transform reasoning_content field
        add_empty_content_when_reasoning_only: Inject placeholder if only reasoning
        reasoning_placeholder_content: Content for placeholder injection
        request_timeout: Default timeout in seconds
        performance_logs_dir: Directory for performance metrics logs
    """
    ctx_len: int = 8192
    max_tokens: int = 4096
    summary_enabled: bool = True
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""
    request_timeout: float = 120.0  # overridden by DEFAULT_REQUEST_TIMEOUT
    performance_logs_dir: str = "__performance_logs"
    # Accepted client credentials for routes that do not override this value.
    # This differs from Route.api_key, which authenticates KRM to upstream.
    api_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelConfig:
    """Configuration specific to a model - can override defaults.
    
    Attributes:
        ctx_len: Context length (uses _UNSET if inheriting from default)
        max_tokens: Max completion tokens (uses _UNSET if inheriting)
        summary_enabled: Summary flag (uses _UNSET if inheriting)
    """
    ctx_len: int | None = None
    max_tokens: int | None = None
    summary_enabled: bool | None = None


@dataclass(frozen=True)
class Route:
    """Represents a routing rule with model settings and fallback chain.
    
    Attributes:
        name: Route identifier
        pattern: Pattern to match (e.g., "local/quick", "pass/*")
        summary_enabled: Whether summarization is enabled for this route
        passthrough_enabled: Whether passthrough mode is enabled
        model: Upstream model to use
        summary_model: Model to use for summarization
        ctx_len: Context length override
        max_tokens: Max tokens override
        transform_reasoning_content: Qwen3.5 reasoning transformation
        add_empty_content_when_reasoning_only: Placeholder injection flag
        reasoning_placeholder_content: Placeholder text
        tool_rewrite_enabled: Tool call rewriting enabled
        tool_rewrite_patterns: Patterns to match for tool rewriting
        model_pattern: Pattern to extract model from matched path
        upstream_url: Custom upstream URL (uses _UNSET if inheriting)
        upstream_headers: Custom headers for this route
        fallback_chain: List of fallback routes/models
        circuit_breaker_enabled: Circuit breaker flag
        failure_threshold: Failure count before breaking
        recovery_timeout: Seconds to wait before retrying
        request_timeout: Request timeout in seconds (uses _UNSET if inheriting)
        cost_priority: Cost priority for fallback selection
        capabilities: Static capabilities exposed by this route
        extends: Name of route to extend from
        _is_private: Whether this is a private/internal route
        _route_hierarchy: Full route path for logging/debugging
    """
    name: str
    pattern: str  # Pattern to match (e.g., "local/quick", "pass/*")

    # Core settings - use sentinel for inheritance detection, but provide defaults
    summary_enabled: bool | None = None
    passthrough_enabled: bool | None = None

    # Model configuration - can reference models dict or specify directly
    model: Optional[str] = None
    summary_model: Optional[str] = None

    # Settings that can be overridden at route level (will fall back to model config)
    ctx_len: int | None = None
    max_tokens: int | None = None

    # Reasoning content handling - use sentinel for inheritance detection, but provide defaults
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""

    # Tool-call rewriting settings (optional)
    tool_rewrite_enabled: bool = False
    tool_rewrite_patterns: List[str] = field(default_factory=lambda: ["nested", "separate"])

    # Model pattern for passthrough - extracts model from matched path
    model_pattern: Optional[str] = None

    # Upstream configuration (for passthrough) - use sentinel
    upstream_url: Optional[str] = None
    upstream_headers: Dict[str, str] = field(default_factory=dict)  # Custom headers for this route
    api_key: Optional[str] = None  # Bearer token for upstream auth (fluisce nei filtri)
    # Accepted client credentials. None inherits; [] explicitly makes public.
    api_keys: Optional[List[str]] = None

    # Fallback chain for automatic rerouting - use sentinel
    fallback_chain: List[Union[str, Dict[str, Any]]] = field(default_factory=list)

    # Circuit breaker settings (optional) - use sentinel
    circuit_breaker_enabled: bool = False
    failure_threshold: int = 3
    recovery_timeout: int = 60

    # Request timeout in seconds (connect + read) - use sentinel
    request_timeout: float | None = None
    request_timeout_inherited: bool = False  # Track if timeout was inherited from parent

    # Performance logs directory override for this route (optional)
    performance_logs_dir: Optional[str] = None

    # Cost priority for fallbacks (lower = higher priority) - use sentinel
    cost_priority: int = 999

    # Static route capabilities for clients and operational status. None means
    # inherit; an explicit empty list intentionally clears a parent's value.
    capabilities: Optional[List[str]] = None

    # Route composition - extend another route and override settings
    extends: Optional[str] = None

    # Parameter overrides to apply to upstream requests (replace downstream values)
    overrides: Dict[str, Any] = field(default_factory=dict)

    # Canonical filter configuration for this route.
    filters: Optional[Dict[str, Any]] = None

    # Track if this route is private (@private decorator)
    _is_private: bool = False

    # Track the full route hierarchy path (e.g., "arkai/lmstudio -> arkai/RTX3090/Qwen3.5-35b")
    _route_hierarchy: List[str] = field(default_factory=list, repr=False)


@dataclass(frozen=True)
class RouteMatch:
    """Result of route matching - contains matched route and extracted model.
    
    Attributes:
        route: The matched Route object
        model: The actual model to use (extracted from pattern if needed)
        capture_groups: Named groups captured from the regex match
    """
    route: Route
    model: str  # The actual model to use (extracted from pattern if needed)
    capture_groups: Dict[str, str] = field(default_factory=dict)


# Minimal fallback route for unmatched models
DEFAULT_FALLBACK_ROUTE = Route(
    name="builtin/fallback-default",
    pattern="*",
    model="",
    ctx_len=8192,
    max_tokens=4096,
    passthrough_enabled=False,
)
__all__ = [
        "DefaultSettings",
    "ModelConfig",
    "Route",
    "RouteMatch",
]
