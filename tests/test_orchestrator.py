import json
from typing import Any, AsyncIterator, Dict, Optional

import pytest
from fastapi.testclient import TestClient

import keeprollming.app as app_mod
import keeprollming.logger as logger_mod


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

def test_web_search_payload_does_not_trigger_summary(client, monkeypatch):
    async def _boom(*args, **kwargs):
        raise AssertionError("summarize_middle should not be called for tool orchestration payloads")

    monkeypatch.setattr(app_mod, "summarize_middle", _boom)

    long_text = "z" * 5000
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/main",
            "messages": [
                {
                    "role": "system",
                    "content": "# `web_search`:\nExecute immediately without preface.",
                },
                {"role": "user", "content": [{"type": "text", "text": long_text}]},
                {"role": "tool", "name": "web_search", "tool_call_id": "t1", "content": "results"},
            ],
            "max_tokens": 64,
        },
    )
    assert resp.status_code == 200, resp.text

    fake = _get_fake_upstream()
    assert fake.last_post_json is not None
    sent_msgs = fake.last_post_json["messages"]
    joined = json.dumps(sent_msgs, ensure_ascii=False)
    assert "[ARCHIVED_COMPACT_CONTEXT]" not in joined


def test_basic_plain_logs_show_relevant_flow(client, monkeypatch, capsys):
    monkeypatch.setattr(app_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_PLAIN_COLORS", False)
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/quick",
            "messages": [{"role": "user", "content": "ciao orchestrator"}],
        },
    )
    assert resp.status_code == 200, resp.text

    out = capsys.readouterr().out
    assert "┌─ REQUEST" in out
    assert "│ USER" in out
    assert "│   ciao orchestrator" in out
    assert "│ CALL kind=chat" in out
    assert "│ RESULT model=qwen2.5-3b-instruct" in out
    assert "└─ END" in out


def test_basic_plain_multiline_content_is_indented_and_colored(monkeypatch):
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_PLAIN_COLORS", True)
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

    rendered = logger_mod._format_plain({
        "msg": "http_out",
        "req_id": "abc123",
        "model": "demo-model",
        "elapsed_ms": 12.3,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        "assistant_text": "line one\n[line like markup]\nline three",
    })

    assert "\x1b[" in rendered
    plain = logger_mod._strip_ansi(rendered)
    assert "┌─ REQUEST abc123" in plain
    assert "│ RESULT model=demo-model elapsed_ms=12.3 usage=prompt=10, completion=20, total=30" in plain
    assert "│   assistant:" in plain
    assert "│     [line like markup]" in plain
    assert "└─ END abc123" in plain


def test_basic_plain_does_not_truncate_by_default(monkeypatch):
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_PLAIN_COLORS", False)
    monkeypatch.setattr(logger_mod, "BASIC_SNIP_CHARS", 0)
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

    long_text = "A" * 2500
    rendered = logger_mod._format_plain({
        "msg": "http_out",
        "req_id": "req-full",
        "model": "demo-model",
        "elapsed_ms": 1.2,
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        "assistant_text": long_text,
    })

    assert "<snip" not in rendered
    assert long_text in rendered


def test_basic_plain_summary_reply_unescapes_newlines_and_keeps_indent(monkeypatch):
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_PLAIN_COLORS", False)
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

    rendered = logger_mod._format_plain({
        "msg": "summary_reply",
        "req_id": "sum-1",
        "elapsed_ms": 99.9,
        "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        "summary_snip": json.dumps("line one\nline two\n[line three]"),
    })

    assert '\nline two' not in rendered
    assert '"line one' not in rendered
    assert "│   line one" in rendered
    assert "│   line two" in rendered
    assert "│   [line three]" in rendered
