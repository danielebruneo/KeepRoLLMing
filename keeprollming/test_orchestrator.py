import json
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

    def stream(self, method: str, url: str, payload: Dict[str, Any]) -> _FakeStreamCtx:
        assert method == "POST"
        self.last_stream_url = url
        self.last_stream_json = payload
        evt = {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": str(payload.get("model", "m")),
            "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        }
        chunks = [
            ("data: " + json.dumps(evt) + "\n\n").encode("utf-8"),
            b"data: [DONE]\n\n",
        ]
        return _FakeStreamCtx(_FakeStreamResponse(chunks))


@pytest.fixture
def client(monkeypatch) -> TestClient:
    # Ensure the app doesn't try to talk to a real upstream.
    fake = _FakeAsyncClient()

    async def _fake_http_client():
        return fake

    async def _fake_ctx(_model: str) -> int:
        # Force a small effective context so long prompts trigger summarization.
        return 512

    monkeypatch.setattr(app_mod, "http_client", _fake_http_client)
    monkeypatch.setattr(app_mod, "get_ctx_len_for_model", _fake_ctx)

    # Expose fake client to tests
    monkeypatch.setattr(app_mod, "_TEST_FAKE_UPSTREAM", fake, raising=False)

    return TestClient(app_mod.app)


def _get_fake_upstream() -> _FakeAsyncClient:
    # Stored on module during fixture setup
    return getattr(app_mod, "_TEST_FAKE_UPSTREAM")


def test_passthrough_model_routes_without_summary(client, monkeypatch):
    # If summarize_middle is called in passthrough mode, the test should fail.
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

    # Long prompt to exceed threshold and trigger summary (ctx_eff mocked to 512 => threshold >=256)
    long_text = "y" * 4000
    messages = [{"role": "user", "content": long_text} for _ in range(6)]

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local/main", "messages": messages},
    )
    assert resp.status_code == 200, resp.text

    fake = _get_fake_upstream()
    assert fake.last_post_json is not None
    sent_msgs = fake.last_post_json["messages"]
    # Ensure the inserted summary message is present
    joined = json.dumps(sent_msgs, ensure_ascii=False)
    assert "SOMMARIO-TEST" in joined

def test_first_user_prompt_is_preserved_raw_in_repacked_messages(client, monkeypatch):
    async def _fake_summary(_middle, **kwargs):
        return "SOMMARIO-TEST"

    monkeypatch.setattr(app_mod, "summarize_middle", _fake_summary)

    long_text = "z" * 2500
    messages = [
        {"role": "system", "content": "BASE SYSTEM RULES"},
        {"role": "user", "content": "FOUNDATIONAL USER PROMPT"},
        {"role": "assistant", "content": long_text},
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": long_text},
        {"role": "user", "content": "ultima richiesta"},
    ]

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local/main", "messages": messages},
    )
    assert resp.status_code == 200, resp.text

    fake = _get_fake_upstream()
    sent_msgs = fake.last_post_json["messages"]

    assert sent_msgs[0]["role"] == "system"
    assert sent_msgs[0]["content"] == "BASE SYSTEM RULES"
    assert any(m["role"] == "user" and m.get("content") == "FOUNDATIONAL USER PROMPT" for m in sent_msgs)

    archived_idx = next(i for i, m in enumerate(sent_msgs) if m["role"] == "system" and "[ARCHIVED_COMPACT_CONTEXT]" in m.get("content", ""))
    first_user_idx = next(i for i, m in enumerate(sent_msgs) if m["role"] == "user" and m.get("content") == "FOUNDATIONAL USER PROMPT")
    assert first_user_idx < archived_idx

    archived_block = sent_msgs[archived_idx]["content"]
    assert "FOUNDATIONAL USER PROMPT" not in archived_block
