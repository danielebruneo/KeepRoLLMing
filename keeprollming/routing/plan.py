"""Immutable per-request execution plan for a resolved route.

``Route`` remains the configuration/inheritance input.  ``RoutePlan`` is the
runtime boundary: it snapshots the resolved settings used by endpoints and
offers a single factory for the request-scoped pipeline.  This prevents the
streaming and non-streaming paths from independently re-reading a mutable
``filters`` configuration.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..core.config_types import RouteSettings


def _freeze(value: Any) -> Any:
    """Recursively freeze route-derived configuration for one request."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Return a fresh mutable configuration object for a pipeline instance."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return deepcopy(value)


@dataclass(frozen=True)
class RoutePlan:
    """Resolved, immutable route decisions for one chat-completion request."""

    route: Any
    client_model: str
    model: str
    route_name: str
    requested_route_name: str
    reroute_chain: tuple[str, ...]
    route_hierarchy: tuple[str, ...]
    settings: RouteSettings
    context_window: int
    default_max_tokens: int
    upstream_url: str
    endpoint_url: str
    upstream_headers: Mapping[str, str]
    filters: Mapping[str, Any]
    enabled_filters: tuple[str, ...]
    capabilities: tuple[str, ...]
    fallback_attempts: tuple["UpstreamAttempt", ...]
    overrides: Mapping[str, Any]

    @classmethod
    def compile(
        cls,
        *,
        route: Any,
        client_model: str,
        model: str,
        settings: RouteSettings,
        context_window: int,
        default_max_tokens: int,
        upstream_url: str,
        routes_by_name: Mapping[str, Any] | None = None,
        defaults: Any = None,
        requested_route_name: str | None = None,
        reroute_chain: tuple[str, ...] | None = None,
        attempt_routes: Sequence[tuple[Any, str]] | None = None,
        preserved_filters: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> "RoutePlan":
        """Compile route-derived runtime decisions after route resolution."""
        from ..orchestrator.pipeline import Pipeline
        from .router import resolve_fallback_chain

        normalized_url = upstream_url.rstrip("/")
        base_url = normalized_url[:-3] if normalized_url.endswith("/v1") else normalized_url
        frozen_filters = _freeze(settings.filters or {})
        mutable_filters = _thaw(frozen_filters)
        if preserved_filters:
            # The selector explicitly owns these named modules. Merge only
            # those modules; all other target filters remain target-owned.
            for name, source_config in preserved_filters.items():
                target_config = mutable_filters.get(name)
                if isinstance(target_config, Mapping) and isinstance(source_config, Mapping):
                    merged = dict(target_config)
                    merged.update(_thaw(source_config))
                    mutable_filters[name] = merged
                else:
                    mutable_filters[name] = _thaw(source_config)
        frozen_filters = _freeze(mutable_filters)
        if attempt_routes is None:
            route_attempts = resolve_fallback_chain(
                route,
                settings.upstream_model,
                routes_by_name=dict(routes_by_name or {}),
                defaults=defaults,
            )
        else:
            route_attempts = list(attempt_routes)
        resolved_attempts = [
            UpstreamAttempt.from_route(candidate_route, candidate_model, defaults)
            for candidate_route, candidate_model in route_attempts
        ]
        # ``upstream_url`` has already incorporated endpoint-level root/env
        # fallback rules. Keep the primary plan aligned with that decision.
        primary_base = normalized_url[:-3] if normalized_url.endswith("/v1") else normalized_url
        if resolved_attempts:
            resolved_attempts[0] = replace(
                resolved_attempts[0],
                upstream_url=normalized_url,
                endpoint_url=f"{primary_base}/v1/chat/completions",
            )
        return cls(
            route=route,
            client_model=client_model,
            model=model,
            route_name=settings.route_name or getattr(route, "name", ""),
            requested_route_name=(requested_route_name or settings.route_name or getattr(route, "name", "")),
            reroute_chain=tuple(reroute_chain or (settings.route_name or getattr(route, "name", ""),)),
            route_hierarchy=tuple(getattr(route, "_route_hierarchy", ()) or ()),
            settings=settings,
            context_window=context_window,
            default_max_tokens=default_max_tokens,
            upstream_url=upstream_url,
            endpoint_url=f"{base_url}/v1/chat/completions",
            upstream_headers=MappingProxyType(dict(settings.upstream_headers)),
            filters=frozen_filters,
            enabled_filters=tuple(Pipeline.enabled_filter_names(mutable_filters)),
            capabilities=tuple(settings.capabilities),
            fallback_attempts=tuple(resolved_attempts),
            overrides=_freeze(getattr(route, "overrides", {}) or {}),
        )

    def build_pipeline(self):
        """Create the mutable pipeline instance for this one request only."""
        from ..orchestrator.pipeline import Pipeline

        return Pipeline.from_route_config(_thaw(self.filters), api_key=self.settings.api_key) or Pipeline()

    def build_upstream_headers(self) -> dict[str, str]:
        """Return fresh request headers, including route API auth when needed."""
        headers = dict(self.upstream_headers)
        if self.settings.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    def build_overrides(self) -> dict[str, Any]:
        """Return the request-local override mapping used for payload mutation."""
        return _thaw(self.overrides)

    @property
    def upstream_model(self) -> str:
        """Resolved upstream chat model for this request."""
        return self.settings.upstream_model

    @property
    def summary_model(self) -> str:
        """Resolved summary model for this request."""
        return self.settings.summary_model

    @property
    def request_timeout(self) -> float:
        """Resolved upstream timeout for this request."""
        return self.settings.request_timeout


@dataclass(frozen=True)
class UpstreamAttempt:
    """One fully resolved upstream retry target for a request."""

    route: Any
    route_name: str
    model: str
    upstream_url: str
    endpoint_url: str
    upstream_headers: Mapping[str, str]
    request_timeout: float

    @classmethod
    def from_route(cls, route: Any, model: str, defaults: Any = None) -> "UpstreamAttempt":
        from .router import get_route_settings

        settings = get_route_settings(route, model, defaults=defaults)
        upstream_url = (settings.upstream_url or "").rstrip("/")
        base_url = upstream_url[:-3] if upstream_url.endswith("/v1") else upstream_url
        headers = dict(settings.upstream_headers)
        if settings.api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        return cls(
            route=route,
            route_name=settings.route_name or getattr(route, "name", ""),
            model=model,
            upstream_url=upstream_url,
            endpoint_url=f"{base_url}/v1/chat/completions",
            upstream_headers=MappingProxyType(headers),
            request_timeout=settings.request_timeout,
        )
