"""Regression coverage for root request-timeout inheritance."""

from __future__ import annotations

import keeprollming.config as config
from keeprollming.routing import Route, get_route_settings, resolve_inherited_route
from keeprollming.types import DefaultSettings


def _route(*, request_timeout: float | None = None) -> Route:
    return Route(
        name="chat/main",
        pattern="chat/main",
        model="backend-model",
        request_timeout=request_timeout,
    )


def test_route_inherits_request_timeout_from_root_defaults() -> None:
    settings = get_route_settings(
        _route(),
        "backend-model",
        defaults=DefaultSettings(request_timeout=1200.0),
    )

    assert settings.request_timeout == 1200.0


def test_route_request_timeout_overrides_root_defaults() -> None:
    settings = get_route_settings(
        _route(request_timeout=300.0),
        "backend-model",
        defaults=DefaultSettings(request_timeout=1200.0),
    )

    assert settings.request_timeout == 300.0


def test_application_config_resolver_uses_current_root_timeout(monkeypatch) -> None:
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings(request_timeout=1200.0))

    settings = config.get_route_settings(_route(), "backend-model")

    assert settings.request_timeout == 1200.0


def test_inherited_route_does_not_materialize_standalone_timeout() -> None:
    parent = _route()
    parent = Route(**{**parent.__dict__, "name": "base", "pattern": "base"})
    child = Route(
        name="chat/main",
        pattern="chat/main",
        extends="base",
        model="backend-model",
    )

    resolved = resolve_inherited_route(child, {"base": parent, "chat/main": child})
    settings = get_route_settings(
        resolved,
        "backend-model",
        defaults=DefaultSettings(request_timeout=1200.0),
    )

    assert resolved.request_timeout is None
    assert settings.request_timeout == 1200.0
