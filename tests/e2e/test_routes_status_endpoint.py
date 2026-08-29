"""Contract coverage for the private dashboard route-status endpoint."""

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
            "name", "upstream_url", "upstream_model", "model_mode",
            "capabilities", "ctx_len", "max_tokens", "activity", "errors",
            "pending_requests", "active_requests", "performance",
        } <= route.keys()
        assert len(route["activity"]) == 60
        assert all("requests" in bucket and "minute" in bucket for bucket in route["activity"])
        assert {
            "samples", "avg_prompt_tps", "avg_completion_tps", "avg_ttft_ms",
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
