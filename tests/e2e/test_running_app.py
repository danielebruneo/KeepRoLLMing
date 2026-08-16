"""Minimal ASGI startup smoke test.

This replaces the former script-like test which imported retired global
configuration profiles but never started or exercised the application.
"""

from fastapi.testclient import TestClient

from keeprollming.app import app


def test_asgi_application_starts_and_serves_openapi():
    """The configured application can start and expose its API contract."""
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/v1/chat/completions" in response.json()["paths"]
