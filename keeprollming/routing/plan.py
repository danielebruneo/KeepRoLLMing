"""Immutable per-request execution plan for a resolved route.

``Route`` remains the configuration/inheritance input.  ``RoutePlan`` is the
runtime boundary: it snapshots the resolved settings used by endpoints and
offers a single factory for the request-scoped pipeline.  This prevents the
streaming and non-streaming paths from independently re-reading a mutable
``filters`` configuration.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

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
    fallback_attempts: tuple[tuple[Any, str], ...]
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
    ) -> "RoutePlan":
        """Compile route-derived runtime decisions after route resolution."""
        from ..orchestrator.pipeline import Pipeline
        from .router import resolve_fallback_chain

        normalized_url = upstream_url.rstrip("/")
        base_url = normalized_url[:-3] if normalized_url.endswith("/v1") else normalized_url
        frozen_filters = _freeze(settings.filters or {})
        mutable_filters = _thaw(frozen_filters)
        return cls(
            route=route,
            client_model=client_model,
            model=model,
            route_name=settings.route_name or getattr(route, "name", ""),
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
            fallback_attempts=tuple(resolve_fallback_chain(route, settings.upstream_model)),
            overrides=_freeze(getattr(route, "overrides", {}) or {}),
        )

    def build_pipeline(self):
        """Create the mutable pipeline instance for this one request only."""
        from ..orchestrator.pipeline import Pipeline

        return Pipeline.from_route_config(
            _thaw(self.filters), api_key=self.settings.api_key
        ) or Pipeline()

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
