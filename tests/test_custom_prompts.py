"""
Test suite for custom summary prompts functionality
"""

import pytest
from fastapi.testclient import TestClient
import keeprollming.app as app_mod


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data: dict = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._json_data


class _FakeAsyncClient:
    async def post(self, url: str, json: dict) -> _FakeResponse:
        return _FakeResponse(
            status_code=200,
            json_data={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": json.get("model", "unknown"),
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def stream(self, method: str, url: str, json: dict = None, payload: dict = None):
        class _FakeStreamCtx:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return None
        
        return _FakeStreamCtx()


@pytest.fixture
def client(monkeypatch) -> TestClient:
    fake = _FakeAsyncClient()

    async def _fake_http_client():
        return fake

    async def _fake_ctx(_model: str) -> int:
        return 512

    monkeypatch.setattr(app_mod, "http_client", _fake_http_client)
    monkeypatch.setattr(app_mod, "get_ctx_len_for_model", _fake_ctx)

    return TestClient(app_mod.app)


@pytest.mark.non_parallelizable
def test_custom_prompt_in_payload(client):
    """Test that custom prompt is parsed from request payload"""
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "test message"}
        ],
        "summary_prompt": "This is a custom prompt"
    })

    # Should not fail with parsing errors
    assert response.status_code == 200


@pytest.mark.non_parallelizable
def test_custom_prompt_with_type(client):
    """Test that custom prompt works when both type and text are provided"""
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "test message"}
        ],
        "summary_prompt_type": "custom",
        "summary_prompt": "This is a custom prompt"
    })

    # Should not fail with parsing errors
    assert response.status_code == 200


def test_default_behavior_without_custom_prompt(client):
    """Test that default behavior works when no custom prompt is provided"""
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "test message"}
        ]
    })

    # Should not fail with parsing errors
    assert response.status_code == 200


def test_passthrough_with_custom_prompt(client):
    """Test that passthrough mode works with custom prompt"""
    response = client.post("/v1/chat/completions", json={
        "model": "pass/test-model",
        "messages": [
            {"role": "user", "content": "test message"}
        ],
        "summary_prompt": "This is a custom prompt"
    })

    # Should not fail with parsing errors
    assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])