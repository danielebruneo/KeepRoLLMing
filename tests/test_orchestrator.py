import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import pytest
from fastapi.testclient import TestClient

import keeprollming.app as app_mod


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data: Optional[Dict[str, Any]] = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Dict[str, Any]:
        return self._json_data


class _FakeStreamResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.headers = {"content-type": "text/event-stream"}
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for c in self._chunks:
            yield c


class _FakeStreamCtx:
    def __init__(self, resp: _FakeStreamResponse):
        self._resp = resp

    async def __aenter__(self) -> _FakeStreamResponse:
        return self._resp

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.last_post_url: Optional[str] = None
        self.last_post_json: Optional[Dict[str, Any]] = None
        self.last_stream_url: Optional[str] = None
        self.last_stream_json: Optional[Dict[str, Any]] = None

    async def post(self, url: str, json: Dict[str, Any]) -> _FakeResponse:
        self.last_post_url = url
        self.last_post_json = json
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

    def stream(self, method: str, url: str, json: Dict[str, Any]) -> _FakeStreamCtx:
        assert method == "POST"
        self.last_stream_url = url
        self.last_stream_json = json
        evt = {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": str(json.get("model", "m")),
            "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        }
        chunks = [
            ("data: " + jsonlib.dumps(evt) + "\n\n").encode("utf-8"),
            b"data: [DONE]\n\n",
        ]
        return _FakeStreamCtx(_FakeStreamResponse(chunks))


jsonlib = json


@pytest.fixture
def client(monkeypatch, tmp_path: Path) -> TestClient:
    fake = _FakeAsyncClient()

    async def _fake_http_client():
        return fake

    async def _fake_ctx(model: str) -> int:
        if "1.5b" in model or "3b" in model:
            return 2048
        return 512

    monkeypatch.setattr(app_mod, "http_client", _fake_http_client)
    monkeypatch.setattr(app_mod, "get_ctx_len_for_model", _fake_ctx)
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_DIR", str(tmp_path / "summary_cache"))
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_ENABLED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_MODE", "cache_append")
    monkeypatch.setattr(app_mod, "_TEST_FAKE_UPSTREAM", fake, raising=False)
    return TestClient(app_mod.app)


def _get_fake_upstream() -> _FakeAsyncClient:
    return getattr(app_mod, "_TEST_FAKE_UPSTREAM")


def test_passthrough_model_routes_without_summary(client, monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("summarize_middle should not be called for pass/* models")

    monkeypatch.setattr(app_mod, "summarize_middle", _boom)

    long_text = "x" * 2000
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "pass/my-backend-model",
            "messages": [{"role": "user", "content": long_text}, {"role": "user", "content": long_text}],
        },
    )
    assert resp.status_code == 200, resp.text

    fake = _get_fake_upstream()
    assert fake.last_post_json is not None
    assert fake.last_post_json["model"] == "my-backend-model"


def test_streaming_sse_proxy(client):
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/quick",
            "stream": True,
            "messages": [{"role": "user", "content": "ciao"}],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "data:" in body
    assert "[DONE]" in body


def test_rolling_summary_trigger_repacked_messages(client, monkeypatch):
    async def _fake_summary(_middle, **kwargs):
        return "SOMMARIO-TEST"

    monkeypatch.setattr(app_mod, "summarize_middle", _fake_summary)

    long_text = "y" * 4000
    messages = [{"role": "user", "content": long_text} for _ in range(6)]

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local/main", "messages": messages},
    )
    assert resp.status_code == 200, resp.text

    fake = _get_fake_upstream()
    assert fake.last_post_json is not None
    joined = json.dumps(fake.last_post_json["messages"], ensure_ascii=False)
    assert "SOMMARIO-TEST" in joined


def test_cache_reuse_by_librechat_conversation_id(client, monkeypatch):
    calls = {"n": 0}

    async def _fake_summary(_middle, **kwargs):
        calls["n"] += 1
        return f"SUMMARY-{calls['n']}"

    monkeypatch.setattr(app_mod, "summarize_middle", _fake_summary)

    messages = [
        {"role": "user", "content": "U0 " + "x" * 1200},
        {"role": "assistant", "content": "A0 " + "x" * 1200},
        {"role": "user", "content": "U1 " + "x" * 1200},
        {"role": "assistant", "content": "A1 " + "x" * 1200},
        {"role": "user", "content": "U2 latest"},
    ]
    headers = {
        "x-librechat-user-id": "user-1",
        "x-librechat-conversation-id": "conv-1",
        "x-librechat-message-id": "m-1",
        "x-librechat-parent-message-id": "p-1",
    }

    r1 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": messages}, headers=headers)
    assert r1.status_code == 200
    assert calls["n"] == 1

    r2 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": messages}, headers=headers)
    assert r2.status_code == 200
    assert calls["n"] == 1

    fake = _get_fake_upstream()
    sent = fake.last_post_json["messages"]
    assert any(m.get("role") == "system" and "ARCHIVED_COMPACT_CONTEXT" in str(m.get("content")) for m in sent)


def test_multimodal_user_content_is_flattened_for_upstream(client):
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/quick",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "image_url", "image_url": {"url": "x"}},
                        {"type": "text", "text": "world"},
                    ],
                }
            ],
        },
    )
    assert resp.status_code == 200
    fake = _get_fake_upstream()
    assert fake.last_post_json is not None
    assert fake.last_post_json["messages"][0]["content"] == "hello\nworld"
