"""Boundary coverage for the broad, pre-parse request-body safety guard."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from keeprollming.app import RequestBodyTooLargeError, _read_json_request, app


def test_chat_rejects_declared_oversized_body_before_route_processing(monkeypatch) -> None:
    """A declared oversize body receives a stable 413 without contacting upstream."""
    from keeprollming import config

    monkeypatch.setitem(config.CONFIG["request_limits"], "max_body_bytes", 16)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            content=b'{"messages":["this is too large"]}',
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_chunked_oversized_body_is_stopped_while_reading(monkeypatch) -> None:
    """No Content-Length is required for the OOM guard to apply."""
    from keeprollming import config

    monkeypatch.setitem(config.CONFIG["request_limits"], "max_body_bytes", 8)
    messages = iter(
        [
            {"type": "http.request", "body": b'{"a":', "more_body": True},
            {"type": "http.request", "body": b'"too long"}', "more_body": False},
        ]
    )

    async def receive() -> dict:
        return next(messages)

    request = Request(
        {"type": "http", "method": "POST", "headers": [], "path": "/v1/chat/completions"},
        receive,
    )
    with pytest.raises(RequestBodyTooLargeError):
        asyncio.run(_read_json_request(request))


def test_invalid_request_limit_falls_back_without_rejecting_configuration(caplog) -> None:
    """A bad safety setting is isolated rather than making configuration unusable."""
    from keeprollming.config import (
        DEFAULT_MAX_REQUEST_BODY_BYTES,
        _normalize_request_limits,
    )

    config = {"request_limits": {"max_body_bytes": "not-a-size"}}
    _normalize_request_limits(config)

    assert config["request_limits"]["max_body_bytes"] == DEFAULT_MAX_REQUEST_BODY_BYTES
    assert "using default" in caplog.text
