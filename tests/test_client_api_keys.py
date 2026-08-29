"""Client API-key route access-control contracts."""

from fastapi.testclient import TestClient

from keeprollming.app import app
from keeprollming.auth import is_authorized, redact_sensitive_headers
from keeprollming.types import DefaultSettings, Route


def test_authentication_accepts_only_matching_bearer_key() -> None:
    assert is_authorized({}, [])
    assert not is_authorized({}, ["secret"])
    assert not is_authorized({"Authorization": "Basic secret"}, ["secret"])
    assert not is_authorized({"Authorization": "Bearer wrong"}, ["secret"])
    assert is_authorized({"Authorization": "Bearer secret"}, ["secret"])


def test_authentication_redacts_sensitive_headers() -> None:
    assert redact_sensitive_headers({
        "Authorization": "Bearer secret",
        "X-Api-Key": "also-secret",
        "X-Request-ID": "safe",
    }) == {
        "Authorization": "[REDACTED]",
        "X-Api-Key": "[REDACTED]",
        "X-Request-ID": "safe",
    }


def test_route_keys_inherit_and_explicit_empty_list_clears() -> None:
    from keeprollming.routing import resolve_inherited_route

    base = Route(name="base/protected", pattern="base/protected", api_keys=["parent"])
    inherited = Route(name="chat/inherited", pattern="chat/inherited", extends="base/protected")
    public = Route(
        name="chat/public", pattern="chat/public", extends="base/protected", api_keys=[]
    )
    routes = {route.name: route for route in (base, inherited, public)}

    assert resolve_inherited_route(inherited, routes).api_keys == ["parent"]
    assert resolve_inherited_route(public, routes).api_keys == []


def test_global_keys_apply_when_route_does_not_declare_any(monkeypatch) -> None:
    from keeprollming import config

    route = Route(name="chat/default", pattern="chat/default", model="backend")
    monkeypatch.setattr(config, "USER_ROUTES", [route])
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings(api_keys=("global",)))

    resolved, _ = config.resolve_route("chat/default")

    assert resolved is not None
    assert resolved.api_keys == ["global"]


def test_upstream_api_key_inherits_with_route_settings() -> None:
    from keeprollming.core.config_types import RouteSettings
    from keeprollming.routing import resolve_inherited_route

    base = Route(name="base/upstream", pattern="base/upstream", api_key="upstream-secret")
    child = Route(name="chat/child", pattern="chat/child", extends="base/upstream")
    resolved = resolve_inherited_route(child, {base.name: base, child.name: child})

    assert RouteSettings.from_route(resolved, "model").api_key == "upstream-secret"


def test_invalid_client_key_configuration_fails_without_echoing_secret() -> None:
    from keeprollming.config import _validate_client_api_key_configuration

    try:
        _validate_client_api_key_configuration({"api_keys": ["valid", 42]})
    except ValueError as exc:
        assert "array of non-empty strings" in str(exc)
        assert "valid" not in str(exc)
    else:  # pragma: no cover - makes the expected validation explicit
        raise AssertionError("invalid client key configuration was accepted")


def test_protected_chat_is_rejected_before_any_upstream_work(monkeypatch) -> None:
    from keeprollming import config

    route = Route(
        name="chat/protected", pattern="chat/protected", model="backend",
        upstream_url="http://upstream.example", api_keys=["client-secret"],
    )
    monkeypatch.setattr(config, "USER_ROUTES", [route])
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings())

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "chat/protected", "messages": [], "stream": False},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_protected_embeddings_is_rejected_before_any_upstream_work(monkeypatch) -> None:
    from keeprollming import config

    route = Route(
        name="embedding/protected", pattern="embedding/protected", model="backend",
        upstream_url="http://upstream.example", api_keys=["client-secret"],
    )
    monkeypatch.setattr(config, "USER_ROUTES", [route])
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings())

    with TestClient(app) as client:
        response = client.post(
            "/v1/embeddings",
            json={"model": "embedding/protected", "input": "hello"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_models_hides_routes_not_authorized_for_caller(monkeypatch) -> None:
    from keeprollming import config

    protected = Route(
        name="chat/protected", pattern="chat/protected", model="protected",
        api_keys=["client-secret"],
    )
    public = Route(name="chat/public", pattern="chat/public", model="public", api_keys=[])
    monkeypatch.setattr(config, "USER_ROUTES", [protected, public])
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings())

    with TestClient(app) as client:
        anonymous = client.get("/v1/models").json()["data"]
        authorized = client.get(
            "/v1/models", headers={"Authorization": "Bearer client-secret"}
        ).json()["data"]

    assert [item["id"] for item in anonymous] == ["chat/public"]
    assert [item["id"] for item in authorized] == ["chat/protected", "chat/public"]
