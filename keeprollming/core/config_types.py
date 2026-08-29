"""
Typed configuration dataclasses.

Replaces bare dict-based route settings with strongly-typed, documented
dataclasses. No more _UNSET sentinel — every field has an explicit default
or is required.

Usage:
    settings = RouteSettings.resolve(route, defaults, model="qwen-7b")
    print(settings.upstream_model)  # str, always valid
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..types import DefaultSettings, Route


@dataclass(frozen=True)
class FilterConfig:
    """Configuration for a single filter in the pipeline."""
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteSettings:
    """Resolved route settings with all defaults applied.

    This replaces:
        get_route_settings(route, model) → dict
        route_settings.get("key", _UNSET)

    All fields are concrete types — no _UNSET, no None fallback chains.
    Immutable (frozen) — settings are a snapshot of resolved configuration.
    """

    # ── Required fields ────────────────────────────────────────────

    upstream_model: str
    route_name: str = ""

    # ── Optional with explicit defaults ────────────────────────────

    summary_model: str = ""
    ctx_len: int = 8192
    max_tokens: int = 4096
    request_timeout: float = 120.0

    # ── Feature flags ──────────────────────────────────────────────

    summary_enabled: bool = True
    passthrough_enabled: bool = False
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""

    # ── Upstream configuration ────────────────────────────────────

    upstream_url: str | None = None
    upstream_headers: dict[str, str] = field(default_factory=dict)
    api_key: str | None = None
    client_api_keys: list[str] = field(default_factory=list)
    fallback_chain: list[dict[str, str]] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)

    # ── Filters ────────────────────────────────────────────────────

    filters: dict[str, Any] | None = None

    # ── Factory methods ────────────────────────────────────────────

    @classmethod
    def resolve(
        cls,
        route,
        defaults,
        model: str,
        model_overrides: "RouteSettings | None" = None,
    ) -> "RouteSettings":
        """Resolve RouteSettings from the 3-level hierarchy.

        Resolution order (higher overrides lower):
          1. Root-level DefaultSettings
          2. Model-specific overrides (model_overrides, optional)
          3. Route-level fields (from the Route object)

        Args:
            route: Route object (already resolved through extends chain)
            defaults: DefaultSettings instance (root-level defaults)
            model: Resolved upstream model name
            model_overrides: Optional model-specific RouteSettings to layer in
        """
        # Level 1: root defaults
        d_ctx_len = defaults.ctx_len if defaults else 8192
        d_max_tokens = defaults.max_tokens if defaults else 4096
        d_timeout = defaults.request_timeout if defaults else 120.0
        d_summary_enabled = defaults.summary_enabled if defaults else True

        # Level 2: model overrides (if provided)
        m_ctx_len = model_overrides.ctx_len if model_overrides else d_ctx_len
        m_max_tokens = model_overrides.max_tokens if model_overrides else d_max_tokens
        m_timeout = model_overrides.request_timeout if model_overrides else d_timeout
        m_summary_enabled = model_overrides.summary_enabled if model_overrides else d_summary_enabled
        m_upstream_url = model_overrides.upstream_url if model_overrides else None

        # Level 3: route overrides (None means "inherit from level 2")
        resolved_ctx_len = route.ctx_len if route.ctx_len is not None else m_ctx_len
        resolved_max_tokens = route.max_tokens if route.max_tokens is not None else m_max_tokens
        resolved_timeout = route.request_timeout if route.request_timeout is not None else m_timeout
        resolved_summary_enabled = (
            bool(route.summary_enabled)
            if route.summary_enabled is not None
            else m_summary_enabled
        )
        resolved_upstream_url = route.upstream_url if route.upstream_url is not None else m_upstream_url

        summary_model = route.summary_model if route.summary_model is not None else model

        return cls(
            route_name=route.name,
            upstream_model=model,
            summary_model=summary_model,
            ctx_len=resolved_ctx_len,
            max_tokens=resolved_max_tokens,
            request_timeout=resolved_timeout,
            summary_enabled=resolved_summary_enabled,
            passthrough_enabled=bool(route.passthrough_enabled),
            transform_reasoning_content=bool(route.transform_reasoning_content),
            add_empty_content_when_reasoning_only=bool(route.add_empty_content_when_reasoning_only),
            reasoning_placeholder_content=route.reasoning_placeholder_content or "",
            upstream_url=resolved_upstream_url,
            upstream_headers=dict(route.upstream_headers or {}),
            api_key=route.api_key,
            client_api_keys=list(
                route.api_keys if route.api_keys is not None
                else getattr(defaults, "api_keys", ())
            ),
            fallback_chain=list(route.fallback_chain or []),
            capabilities=list(route.capabilities or []),
            filters=route.filters,
        )

    @classmethod
    def from_route(cls, route, model: str) -> "RouteSettings":
        """Build RouteSettings from a resolved Route object (no defaults, convenience).

        Uses hard-coded defaults. Prefer resolve() for production use.
        """
        return cls(
            route_name=route.name,
            upstream_model=model,
            summary_model=route.summary_model if route.summary_model is not None else model,
            ctx_len=route.ctx_len if route.ctx_len is not None else 8192,
            max_tokens=route.max_tokens if route.max_tokens is not None else 4096,
            summary_enabled=bool(route.summary_enabled),
            passthrough_enabled=bool(route.passthrough_enabled),
            transform_reasoning_content=bool(route.transform_reasoning_content),
            add_empty_content_when_reasoning_only=bool(route.add_empty_content_when_reasoning_only),
            reasoning_placeholder_content=route.reasoning_placeholder_content or "",
            upstream_url=route.upstream_url if route.upstream_url is not None else None,
            upstream_headers=dict(route.upstream_headers or {}),
            api_key=route.api_key,
            client_api_keys=list(route.api_keys or []),
            fallback_chain=list(route.fallback_chain or []),
            capabilities=list(route.capabilities or []),
            request_timeout=(
                route.request_timeout
                if route.request_timeout is not None
                else 120.0
            ),
            filters=route.filters,
        )

    @classmethod
    def defaults(cls) -> "RouteSettings":
        """Return default settings (no route configured)."""
        return cls(upstream_model="", route_name="default")
