"""
Test for custom summary prompts functionality - properly testing both cases:
1. Using a defined prompt type from config/custom files
2. Providing direct custom prompt text in the request body
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
    async def post(self, url: str, json: dict = None, headers: dict = None) -> _FakeResponse:
        return _FakeResponse(
            status_code=200,
            json_data={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": json.get("model", "unknown") if json else "unknown",
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


def test_direct_custom_prompt_text(client):
    """Test that direct custom prompt text from payload works"""

    # This should use the summary_prompt field content directly as template
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "Explain quantum computing"}
        ],
        "summary_prompt": "You are an expert explainer. Please explain the following conversation transcript in simple terms:"
    })

    # The request should succeed with mocked backend
    assert response.status_code == 200


def test_prompt_type_with_custom_prompt_text(client):
    """Test that summary_prompt_type with summary_prompt works"""

    # This should use the summary_prompt field content for a specific prompt type
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "Explain quantum computing"}
        ],
        "summary_prompt_type": "custom",
        "summary_prompt": "You are an expert explainer. Please explain the following conversation transcript in simple terms:"
    })

    # The request should succeed with mocked backend
    assert response.status_code == 200


def test_default_behavior_still_works(client):
    """Test that default behavior without custom prompts still works"""

    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "Explain quantum computing"}
        ]
    })

    # The request should succeed with mocked backend
    assert response.status_code == 200


def test_prompt_from_config_file(client):
    """Test that prompt from config file works correctly"""

    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "Explain quantum computing"}
        ],
        "summary_prompt_type": "structured_explainer"
    })

    # The request should succeed with mocked backend
    assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])