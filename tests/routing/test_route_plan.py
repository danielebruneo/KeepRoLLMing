"""Regression tests for the immutable endpoint RoutePlan boundary."""

from __future__ import annotations

import pytest

from keeprollming.core.config_types import RouteSettings
from keeprollming.routing import Route, RoutePlan


def _plan(*, filters=None, upstream_url="http://upstream:8000/v1"):
    route = Route(
        name="chat/main",
        pattern="chat/main",
        model="backend-model",
        upstream_url=upstream_url,
        upstream_headers={"X-Route": "main"},
        api_key="secret",
        filters=filters,
        overrides={"temperature": 0.2},
    )
    settings = RouteSettings.from_route(route, "backend-model")
    return RoutePlan.compile(
        route=route,
        client_model="chat/main",
        model="backend-model",
        settings=settings,
        context_window=32768,
        default_max_tokens=8192,
        upstream_url=upstream_url,
    )


def test_route_plan_snapshots_route_config_and_builds_endpoint_url():
    filters = {
        "timestamp": {"enabled": True, "template": "before"},
        "model_nudge": {"enabled": False},
    }
    plan = _plan(filters=filters)
    filters["timestamp"]["template"] = "after"

    assert plan.endpoint_url == "http://upstream:8000/v1/chat/completions"
    assert plan.route_name == "chat/main"
    assert plan.request_timeout == 120.0
    assert plan.summary_model == "backend-model"
    assert plan.enabled_filters == ("timestamp",)
    assert plan.fallback_attempts == ((plan.route, "backend-model"),)
    assert plan.build_overrides() == {"temperature": 0.2}
    assert plan.filters["timestamp"]["template"] == "before"
    with pytest.raises(TypeError):
        plan.filters["new"] = {}  # type: ignore[index]


def test_route_plan_builds_request_scoped_pipeline_and_headers():
    plan = _plan(filters={"timestamp": {"enabled": True}})

    first = plan.build_pipeline()
    second = plan.build_pipeline()

    assert first is not second
    assert first._stream_filter_config == second._stream_filter_config
    assert plan.build_upstream_headers() == {
        "X-Route": "main",
        "Authorization": "Bearer secret",
    }


def test_route_plan_normalizes_base_url_without_v1_suffix():
    plan = _plan(upstream_url="http://upstream:8000")

    assert plan.endpoint_url == "http://upstream:8000/v1/chat/completions"
