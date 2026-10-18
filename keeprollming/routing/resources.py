"""Derived backend-resource identity and static configuration validation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ..types import DefaultSettings, Route
from .router import resolve_inherited_route


@dataclass(frozen=True)
class BackendResourceKey:
    """One statically identifiable upstream model resource."""

    upstream_url: str
    model: str

    def __str__(self) -> str:
        return f"{self.upstream_url} :: {self.model}"


def backend_resource_key(route: Route) -> BackendResourceKey | None:
    """Derive a physical-resource identity from resolved transport and model.

    Passthrough/dynamic-model routes cannot be grouped until request time and
    intentionally return ``None`` here.
    """
    if not route.upstream_url or not route.model:
        return None
    url = route.upstream_url.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return BackendResourceKey(url, route.model.strip())


def validate_shared_backend_resources(
    routes: Iterable[Route],
    *,
    defaults: DefaultSettings | None = None,
) -> None:
    """Reject conflicting resource-owned settings for the same URL/model.

    Route-specific queue tolerance deliberately is not compared: it represents
    the client/service-class willingness to wait. Capacity and breaker state
    describe the shared backend and must be coherent before a global allocator
    can use them safely.
    """
    route_list = list(routes)
    by_name = {route.name: route for route in route_list}
    groups: dict[BackendResourceKey, list[tuple[str, tuple[object, ...]]]] = defaultdict(list)
    for route in route_list:
        resolved = resolve_inherited_route(route, by_name, defaults=defaults)
        key = backend_resource_key(resolved)
        if key is None:
            continue
        signature = (
            resolved.max_concurrent,
            bool(resolved.circuit_breaker_enabled),
            resolved.failure_threshold,
            resolved.recovery_timeout,
        )
        groups[key].append((route.name, signature))

    for key, members in groups.items():
        expected = members[0][1]
        conflicts = [(name, values) for name, values in members if values != expected]
        if conflicts:
            rendered = "; ".join(f"{name}={values}" for name, values in members)
            raise ValueError(
                "conflicting shared-backend settings for resource "
                f"'{key}': {rendered}. Routes sharing an upstream URL and "
                "resolved model must agree on max_concurrent and circuit "
                "breaker settings."
            )


__all__ = ["BackendResourceKey", "backend_resource_key", "validate_shared_backend_resources"]
