"""Validation and inheritance contracts for route admission settings."""

import pytest

from keeprollming.config import (
    load_user_routes,
    validate_resolved_route_admission,
    validate_shared_backend_resource_settings,
)
from keeprollming.routing.router import resolve_inherited_route
from keeprollming.types import Route


def test_admission_settings_inherit_from_parent_route() -> None:
    base = Route(
        name="base",
        pattern="base",
        max_concurrent=2,
        queue_timeout=1.5,
        routing_priority=25,
        queue_aging={"interval_seconds": 15, "priority_step": 2, "max_boost": 30},
        deadline_policy={"enabled": True, "first_token_budget_ms": 8000},
    )
    child = Route(name="chat/child", pattern="chat/child", extends="base")

    resolved = resolve_inherited_route(child, {"base": base, "chat/child": child})

    assert resolved.max_concurrent == 2
    assert resolved.queue_timeout == 1.5
    assert resolved.routing_priority == 25
    assert resolved.queue_aging == {
        "interval_seconds": 15,
        "priority_step": 2,
        "max_boost": 30,
    }
    assert resolved.deadline_policy == {"enabled": True, "first_token_budget_ms": 8000}


def test_admission_defaults_to_one_slot_when_omitted() -> None:
    route = Route(name="chat/default", pattern="chat/default")

    resolved = resolve_inherited_route(route, {route.name: route})

    assert resolved.max_concurrent == 1
    assert resolved.queue_timeout is None
    assert resolved.routing_priority == 0


@pytest.mark.parametrize(
    "field, value",
    [
        ("max_concurrent", 0),
        ("max_concurrent", True),
        ("queue_timeout", -1),
        ("queue_timeout", "not-a-number"),
        ("routing_priority", True),
        ("routing_priority", "highest"),
        ("queue_aging", {"interval_seconds": 0}),
        ("queue_aging", {"priority_step": 0}),
        ("queue_aging", {"max_boost": -1}),
        ("queue_aging", {"unknown": 1}),
        ("deadline_policy", {"enabled": True}),
        ("deadline_policy", {"enabled": "yes", "first_token_budget_ms": 1000}),
        ("deadline_policy", {"enabled": True, "first_token_budget_ms": 0}),
        ("deadline_policy", {"enabled": True, "first_token_budget_ms": 1000, "unknown": 1}),
    ],
)
def test_invalid_admission_settings_fail_config_validation(field, value) -> None:
    route = Route(name="bad", pattern="bad", **{field: value})

    with pytest.raises(ValueError, match=field):
        validate_resolved_route_admission([route])


def test_shared_backend_requires_consistent_capacity_and_breaker_settings() -> None:
    first = Route(
        name="chat/a",
        pattern="chat/a",
        model="qwen",
        upstream_url="http://backend:8000/v1",
        max_concurrent=1,
    )
    second = Route(
        name="chat/b",
        pattern="chat/b",
        model="qwen",
        upstream_url="http://backend:8000",
        max_concurrent=2,
    )

    with pytest.raises(ValueError, match="conflicting shared-backend settings"):
        validate_shared_backend_resource_settings([first, second])


def test_shared_backend_allows_distinct_client_queue_tolerance() -> None:
    first = Route(
        name="chat/a",
        pattern="chat/a",
        model="qwen",
        upstream_url="http://backend:8000",
        max_concurrent=1,
        queue_timeout=0,
    )
    second = Route(
        name="chat/b",
        pattern="chat/b",
        model="qwen",
        upstream_url="http://backend:8000/v1",
        max_concurrent=1,
        queue_timeout=10,
    )

    validate_shared_backend_resource_settings([first, second])


def test_omitted_circuit_settings_inherit_through_real_config_loader() -> None:
    routes = load_user_routes(
        {
            "routes": {
                "base": {
                    "model": "qwen",
                    "upstream_url": "http://backend:8000",
                    "circuit_breaker_enabled": True,
                    "failure_threshold": 5,
                    "recovery_timeout": 30,
                },
                "child": {"extends": "base"},
            }
        }
    )
    by_name = {route.name: route for route in routes}

    resolved = resolve_inherited_route(by_name["child"], by_name)

    assert resolved.circuit_breaker_enabled is True
    assert resolved.failure_threshold == 5
    assert resolved.recovery_timeout == 30


def test_queue_aging_loads_and_inherits_through_real_config_loader() -> None:
    routes = load_user_routes(
        {
            "routes": {
                "base": {
                    "model": "qwen",
                    "upstream_url": "http://backend:8000",
                    "queue_aging": {
                        "interval_seconds": 12,
                        "priority_step": 3,
                        "max_boost": 24,
                    },
                },
                "child": {"extends": "base"},
            }
        }
    )
    by_name = {route.name: route for route in routes}

    resolved = resolve_inherited_route(by_name["child"], by_name)

    assert resolved.queue_aging == {
        "interval_seconds": 12,
        "priority_step": 3,
        "max_boost": 24,
    }


def test_deadline_policy_loads_and_inherits_through_real_config_loader() -> None:
    routes = load_user_routes(
        {
            "routes": {
                "base": {
                    "model": "qwen",
                    "upstream_url": "http://backend:8000",
                    "deadline_policy": {
                        "enabled": True,
                        "first_token_budget_ms": 8000,
                        "urgency_window_ms": 2000,
                        "max_priority_boost": 15,
                        "ttft_safety_factor": 1.75,
                        "min_samples": 7,
                    },
                },
                "child": {"extends": "base"},
            }
        }
    )
    by_name = {route.name: route for route in routes}

    resolved = resolve_inherited_route(by_name["child"], by_name)

    assert resolved.deadline_policy == {
        "enabled": True,
        "first_token_budget_ms": 8000,
        "urgency_window_ms": 2000,
        "max_priority_boost": 15,
        "ttft_safety_factor": 1.75,
        "min_samples": 7,
    }
