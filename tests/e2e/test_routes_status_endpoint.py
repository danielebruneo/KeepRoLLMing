"""Contract coverage for the private dashboard route-status endpoint."""

import pytest
from fastapi.testclient import TestClient

from keeprollming.app import app
from keeprollming.types import DefaultSettings, Route


def test_routes_status_endpoint_has_dashboard_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/routes")

    assert response.status_code == 200
    payload = response.json()
    assert payload["window_minutes"] == 60
    assert isinstance(payload["routes"], list)
    assert payload["routes"]
    for route in payload["routes"]:
        assert {
            "name",
            "upstream_url",
            "upstream_model",
            "model_mode",
            "capabilities",
            "ctx_len",
            "max_tokens",
            "activity",
            "errors",
            "admission",
            "circuit_breaker",
            "queued_requests",
            "pending_requests",
            "active_requests",
            "performance",
            "balance",
            "balance_policy",
        } <= route.keys()
        assert len(route["activity"]) == 60
        assert all("requests" in bucket and "minute" in bucket for bucket in route["activity"])
        assert {
            "samples",
            "avg_prompt_tps",
            "avg_completion_tps",
            "avg_ttft_ms",
            "avg_elapsed_ms",
        } <= route["performance"].keys()


def test_routes_status_resolves_public_route_configuration(monkeypatch) -> None:
    from keeprollming import config

    base = Route(
        name="base/internal",
        pattern="base/internal",
        model="base-model",
        upstream_url="http://upstream.example",
        ctx_len=32000,
        max_tokens=4096,
        capabilities=["chat", "tools"],
        queue_aging={"interval_seconds": 30, "priority_step": 2, "max_boost": 20},
        _is_private=True,
    )
    public = Route(
        name="chat/public",
        pattern="chat/public",
        extends="base/internal",
        max_tokens=2048,
    )
    monkeypatch.setattr(config, "USER_ROUTES", [base, public])
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings())
    monkeypatch.setattr(config, "UPSTREAM_BASE_URL", "http://fallback.example")

    with TestClient(app) as client:
        response = client.get("/routes")

    assert response.status_code == 200
    routes = response.json()["routes"]
    assert [route["name"] for route in routes] == ["chat/public"]
    route = routes[0]
    assert route["upstream_url"] == "http://upstream.example"
    assert route["upstream_model"] == "base-model"
    assert route["model_mode"] == "configured"
    assert route["capabilities"] == ["chat", "tools"]
    assert route["ctx_len"] == 32000
    assert route["max_tokens"] == 2048
    assert len(route["activity"]) == 60
    assert route["errors"] == []
    assert route["performance"]["samples"] == 0
    assert route["admission"]["routing_priority"] == 0
    assert route["admission"]["queue_aging"] == {
        "interval_seconds": 30,
        "priority_step": 2,
        "max_boost": 20,
    }
    assert route["admission"]["deadline_policy"] is None
    assert route["admission"]["resource"] == {
        "capacity": 1,
        "active": 0,
        "queued": 0,
    }


@pytest.mark.parametrize("selection_policy", ["priority", "least_loaded"])
@pytest.mark.parametrize(
    "promotion", [None, {"lookahead_turns": 0.5, "expected_new_prompt_tokens": 2048, "expected_completion_tokens": 512}]
)
def test_routes_status_marks_balancing_selector_as_dynamic(monkeypatch, selection_policy, promotion) -> None:
    from keeprollming import config

    runner = Route(
        name="code/runner",
        pattern="code/runner",
        model="runner-model",
        upstream_url="http://runner.example",
        ctx_len=32000,
        max_tokens=4096,
        max_concurrent=2,
        allow_idle_reservation=False,
        capabilities=["chat", "tools"],
    )
    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-model",
        upstream_url="http://selector.example",
        balance={
            "targets": ["code/runner"],
            "selection_policy": selection_policy,
            **({"promotion": promotion} if promotion is not None else {}),
            "preserve_overrides": ["reasoning_effort"],
        },
    )
    monkeypatch.setattr(config, "USER_ROUTES", [runner, selector])
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings())

    with TestClient(app) as client:
        response = client.get("/routes")

    assert response.status_code == 200
    route = next(item for item in response.json()["routes"] if item["name"] == "public/code")
    assert route["model_mode"] == "balanced"
    assert route["allow_idle_reservation"] is None
    assert route["upstream_url"] is None
    assert route["upstream_model"] is None
    assert route["balance_policy"] == {
        "targets": ["code/runner"],
        "selection_policy": selection_policy,
        "promotion": {
            "lookahead_turns": 5.0,
            "expected_new_prompt_tokens": 8192,
            "expected_completion_tokens": 1024,
            "expected_current_completion_tokens": 1024,
            "metrics": {
                "recent_min_samples": 3,
                "history_min_samples": 6,
                "history_max_age_seconds": 86400.0,
                "max_history_cv": 0.5,
                "historical_margin_ratio": 0.2,
                "configured_margin_ratio": 0.35,
            },
            **(promotion or {}),
        },
        "preserve_overrides": ["reasoning_effort"],
        "resolved_targets": [
            {
                "route": "code/runner",
                "priority": 0,
                "upstream_url": "http://runner.example",
                "upstream_model": "runner-model",
                "model_mode": "configured",
                "capabilities": ["chat", "tools"],
                "ctx_len": 32000,
                "max_tokens": 4096,
                "resource": "http://runner.example :: runner-model",
                "allow_idle_reservation": False,
                "admission": {
                    "max_concurrent": 2,
                    "routing_priority": 0,
                    "queue_timeout_ms": 0,
                    "queue_aging": None,
                    "deadline_policy": None,
                    "resource": {"capacity": 2, "active": 0, "queued": 0},
                },
                "circuit_breaker": {
                    "enabled": False,
                    "failure_threshold": 3,
                    "recovery_timeout_ms": 60000,
                    "state": "closed",
                    "consecutive_failures": 0,
                },
            }
        ],
    }


def test_routes_status_projects_nested_balancing_selector(monkeypatch) -> None:
    from keeprollming import config

    runner = Route(
        name="code/runner",
        pattern="code/runner",
        model="runner-model",
        upstream_url="http://runner.example",
    )
    dispatcher = Route(
        name="code/dispatch",
        pattern="code/dispatch",
        model="unused-model",
        balance={"targets": ["code/runner"]},
    )
    selector = Route(
        name="public/code",
        pattern="public/code",
        model="unused-model",
        balance={"targets": [{"route": "code/dispatch", "priority": 10}]},
    )
    monkeypatch.setattr(config, "USER_ROUTES", [runner, dispatcher, selector])
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings())

    with TestClient(app) as client:
        response = client.get("/routes")

    assert response.status_code == 200
    route = next(item for item in response.json()["routes"] if item["name"] == "public/code")
    target = route["balance_policy"]["resolved_targets"][0]
    assert target["route"] == "code/dispatch"
    assert target["priority"] == 10
    assert target["model_mode"] == "balanced"
    assert target["upstream_url"] is None
    assert target["upstream_model"] is None
    assert target["balance_policy"]["resolved_targets"][0]["route"] == "code/runner"
    assert target["balance_policy"]["resolved_targets"][0]["upstream_url"] == ("http://runner.example")
