import json
import tempfile
from pathlib import Path
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




def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner

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
def client(monkeypatch, tmp_path) -> TestClient:
    # Ensure the app doesn't try to talk to a real upstream.
    fake = _FakeAsyncClient()

    async def _fake_http_client():
        return fake

    async def _fake_ctx(_model: str) -> int:
        # Force a small effective context so long prompts trigger summarization.
        return 512

    monkeypatch.setattr(app_mod, "http_client", _fake_http_client)
    monkeypatch.setattr(app_mod, "get_ctx_len_for_model", _fake_ctx)
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_DIR", str(tmp_path / "summary_cache"))
    monkeypatch.setattr(app_mod, "SUMMARY_MODE", "cache_append")
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_ENABLED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_CONSOLIDATE_WHEN_NEEDED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_FORCE_CONSOLIDATE", False)
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_FINGERPRINT_MSGS", 1)

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
    assert "│ RESULT model=demo-model" in plain
    assert "elapsed_ms=12.3" in plain
    assert "usage=prompt=10, completion=20, total=30" in plain
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


def test_summary_cache_hit_skips_new_summary_call(client, monkeypatch):
    calls = {"count": 0}

    async def _fake_summary(*args, **kwargs):
        calls["count"] += 1
        return "PREFIX-SUMMARY"

    async def _boom_incremental(*args, **kwargs):
        raise AssertionError("incremental summary should not be called when cache+append fits")

    monkeypatch.setattr(app_mod, "summarize_middle", _fake_summary)
    monkeypatch.setattr(app_mod, "summarize_incremental", _boom_incremental)

    messages = [
        {"role": "user", "content": "A" * 1200},
        {"role": "assistant", "content": "B" * 1200},
        {"role": "user", "content": "C" * 300},
    ]

    resp1 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": messages})
    assert resp1.status_code == 200, resp1.text
    assert calls["count"] == 1

    resp2 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": messages})
    assert resp2.status_code == 200, resp2.text
    assert calls["count"] == 1

    fake = _get_fake_upstream()
    sent_msgs = fake.last_post_json["messages"]
    joined = json.dumps(sent_msgs, ensure_ascii=False)
    assert "PREFIX-SUMMARY" in joined


def test_summary_cache_consolidates_when_needed(client, monkeypatch):
    calls = {"initial": 0, "incremental": 0}

    async def _fake_summary(*args, **kwargs):
        calls["initial"] += 1
        return "SHORT-SUMMARY"

    async def _fake_incremental(existing_summary, new_messages, **kwargs):
        calls["incremental"] += 1
        return existing_summary + "\nAI: merged"

    monkeypatch.setattr(app_mod, "summarize_middle", _fake_summary)
    monkeypatch.setattr(app_mod, "summarize_incremental", _fake_incremental)

    first_messages = [
        {"role": "user", "content": "A" * 1300},
        {"role": "assistant", "content": "B" * 1300},
        {"role": "user", "content": "C" * 200},
    ]
    resp1 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": first_messages})
    assert resp1.status_code == 200, resp1.text

    second_messages = first_messages + [
        {"role": "assistant", "content": "D" * 1400},
        {"role": "user", "content": "E" * 1400},
    ]
    resp2 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": second_messages})
    assert resp2.status_code == 200, resp2.text
    assert calls["incremental"] == 1

    fake = _get_fake_upstream()
    joined = json.dumps(fake.last_post_json["messages"], ensure_ascii=False)
    assert "merged" in joined


def test_basic_plain_highlights_ai_human_chunks(monkeypatch):
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_PLAIN_COLORS", True)
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

    rendered = logger_mod._format_plain({
        "msg": "summary_reply",
        "req_id": "abc123",
        "elapsed_ms": 1.0,
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        "summary_snip": "Human: hello\nAI: hi there",
    })

    assert "\x1b[" in rendered
    plain = logger_mod._strip_ansi(rendered)
    assert "Human: hello" in plain
    assert "AI: hi there" in plain


def test_repacked_keeps_latest_user_when_consolidated(monkeypatch, client):
    async def _fake_summary(*args, **kwargs):
        return "SUMMARY"

    async def _fake_incremental(existing_summary, new_messages, **kwargs):
        return existing_summary + "\nUPDATED"

    monkeypatch.setattr(app_mod, "summarize_middle", _fake_summary)
    monkeypatch.setattr(app_mod, "summarize_incremental", _fake_incremental)

    long_text = "y" * 4000
    messages = [
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": long_text},
        {"role": "assistant", "content": "a3"},
    ]

    resp1 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": messages})
    assert resp1.status_code == 200

    messages2 = messages + [{"role": "user", "content": "ultima domanda"}]
    resp2 = client.post("/v1/chat/completions", json={"model": "local/main", "messages": messages2})
    assert resp2.status_code == 200

    fake = _get_fake_upstream()
    assert fake.last_post_json is not None
    sent_msgs = fake.last_post_json["messages"]
    assert any(m.get("role") == "user" for m in sent_msgs)
    assert sent_msgs[-1].get("role") == "user"
    assert sent_msgs[-1].get("content") == "ultima domanda"


def test_basic_plain_wraps_long_lines(monkeypatch, capsys):
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_PLAIN_COLORS", False)
    monkeypatch.setattr(logger_mod, "LOG_PLAIN_WRAP_WIDTH", 50)
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

    logger_mod.log("INFO", "conv_user", req_id="wraptest", text="AI: " + ("x " * 40))
    out = capsys.readouterr().out
    assert "┌─ REQUEST wraptest" in out
    assert "│   AI:" in out
    assert out.count("│   ") >= 2



def test_cache_append_clamps_max_tokens_and_skips_incremental_when_tail_fits(monkeypatch, tmp_path):
    from keeprollming import app as app_mod

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "u1"},
    ]

    monkeypatch.setattr(app_mod, "SUMMARY_MODE", "cache_append")
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_ENABLED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_DIR", str(tmp_path / "summary_cache"))
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_FINGERPRINT_MSGS", 1)
    monkeypatch.setattr(app_mod, "SUMMARY_FORCE_CONSOLIDATE", False)
    monkeypatch.setattr(app_mod, "SUMMARY_CONSOLIDATE_WHEN_NEEDED", True)
    monkeypatch.setattr(app_mod, "get_ctx_len_for_model", _async_return(2000))

    fp = app_mod.conversation_fingerprint(messages, 1)
    entry = app_mod.make_cache_entry(
        fingerprint=fp,
        start_idx=0,
        end_idx=1,
        messages=[m for m in messages if m["role"] != "system"],
        summary_text="cached summary",
        summary_model="sum-model",
        token_estimate=10,
        source_mode="test",
    )
    app_mod.save_cache_entry(str(tmp_path / "summary_cache"), entry)

    async def _boom(*args, **kwargs):
        raise AssertionError("incremental summary should not be called")

    monkeypatch.setattr(app_mod, "summarize_incremental", _boom)

    sent = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def raise_for_status(self):
            return None
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "model": "main-model"}

    class _Client:
        async def post(self, url, json):
            sent["payload"] = json
            return _Resp()

    monkeypatch.setattr(app_mod, "http_client", _async_return(_Client()))

    payload = {"model": "local/deep", "messages": messages, "stream": False, "max_tokens": 2000}

    import asyncio
    from starlette.requests import Request

    async def _call():
        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": [(b"content-type", b"application/json")]}
        body = json.dumps(payload).encode()
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        req = Request(scope, receive)
        return await app_mod.chat_completions(req)

    resp = asyncio.run(_call())
    assert resp.status_code == 200
    assert sent["payload"]["max_tokens"] < 2000
    roles = [m["role"] for m in sent["payload"]["messages"]]
    assert roles[-1] == "user"


def test_sanitize_summary_text_removes_prompt_echo():
    from keeprollming.rolling_summary import _sanitize_summary_text

    raw = """=== EXISTING SUMMARY START ===
[ARCHIVED_COMPACT_CONTEXT]
EXTRACTION_SUMMARY_START
hello
[/EXTRACTION_SUMMARY_START]
[/ARCHIVED_COMPACT_CONTEXT]
=== EXISTING SUMMARY END ===

=== NEW MESSAGES START ===
USER: test
=== NEW MESSAGES END ==="""
    cleaned = _sanitize_summary_text(raw, fallback="fallback")
    assert "NEW MESSAGES" not in cleaned
    assert "ARCHIVED_COMPACT_CONTEXT" not in cleaned
    assert "EXTRACTION_SUMMARY_START" not in cleaned
    assert cleaned == "hello"


@pytest.mark.asyncio
async def test_summary_middle_overflow_chunks(monkeypatch):
    import keeprollming.rolling_summary as rs

    calls = []

    async def fake_request(body):
        calls.append(body)
        if len(calls) == 1:
            class DummyResp:
                def json(self):
                    return {"error": {"message": "request exceeds the available context size (4096 tokens), try increasing it", "n_ctx": 4096}}
                text = "request exceeds the available context size"
            import httpx
            raise httpx.HTTPStatusError("overflow", request=None, response=DummyResp())
        return {"choices": [{"message": {"content": "SUMMARY OK"}}], "usage": {}}

    async def fake_get_ctx(_model):
        return 4096

    monkeypatch.setattr(rs, '_request_summary_completion', fake_request)
    monkeypatch.setattr(rs, 'get_ctx_len_for_model', fake_get_ctx)

    msgs = [{"role": "user", "content": "A" * 12000}, {"role": "assistant", "content": "B" * 12000}]
    out = await rs.summarize_middle(msgs, req_id='r1', summary_model='sum-model')
    assert out == 'SUMMARY OK'
    assert len(calls) >= 2




@pytest.mark.asyncio
async def test_summary_middle_preflight_ctx_overflow_chunks_without_nameerror(monkeypatch):
    import keeprollming.rolling_summary as rs

    calls = []

    async def fake_request(body):
        calls.append(body)
        return {"choices": [{"message": {"content": "SUMMARY OK"}}], "usage": {}}

    async def fake_get_ctx(_model):
        return 4096

    monkeypatch.setattr(rs, '_request_summary_completion', fake_request)
    monkeypatch.setattr(rs, 'get_ctx_len_for_model', fake_get_ctx)

    msgs = [{"role": "user", "content": "A" * 12000}, {"role": "assistant", "content": "B" * 12000}]
    out = await rs.summarize_middle(msgs, req_id='r-preflight', summary_model='sum-model')
    assert out == 'SUMMARY OK'
    assert len(calls) >= 1
def test_cache_append_preserves_first_user_raw(client, monkeypatch, tmp_path):
    async def _fake_summary(_middle, **kwargs):
        return "COMPACT"

    monkeypatch.setattr(app_mod, 'summarize_middle', _fake_summary)
    monkeypatch.setattr(app_mod, 'SUMMARY_CACHE_DIR', str(tmp_path / 'summary_cache2'))

    messages = [
        {"role": "system", "content": "SYSTEM RULES"},
        {"role": "user", "content": "FOUNDATIONAL USER PROMPT"},
        {"role": "assistant", "content": "a" * 3000},
        {"role": "user", "content": "b" * 3000},
        {"role": "assistant", "content": "c" * 3000},
        {"role": "user", "content": "latest question"},
    ]
    resp = client.post('/v1/chat/completions', json={"model": "local/main", "messages": messages})
    assert resp.status_code == 200, resp.text
    fake = _get_fake_upstream()
    sent = fake.last_post_json['messages']
    assert sent[0]['role'] == 'system'
    joined = json.dumps(sent, ensure_ascii=False)
    assert 'FOUNDATIONAL USER PROMPT' in joined
    archived_idx = next(i for i, m in enumerate(sent) if m['role'] == 'system' and '[ARCHIVED_COMPACT_CONTEXT]' in m.get('content',''))
    first_user_idx = next(i for i, m in enumerate(sent) if m['role'] == 'user' and 'FOUNDATIONAL USER PROMPT' in str(m.get('content','')))
    assert first_user_idx < archived_idx


@pytest.mark.asyncio
async def test_summary_middle_overflow_error_payload_chunks(monkeypatch):
    import keeprollming.rolling_summary as rs

    calls = []

    async def fake_request(body):
        calls.append(body)
        if len(calls) == 1:
            return {
                "error": {
                    "details": {
                        "response": {
                            "error": {
                                "code": 400,
                                "message": "request (4473 tokens) exceeds the available context size (4096 tokens), try increasing it",
                                "n_ctx": 4096,
                                "n_prompt_tokens": 4473,
                                "type": "exceed_context_size_error",
                            }
                        },
                        "status_code": 400,
                    },
                    "message": "llama-server request failed",
                    "type": "backend_error",
                }
            }
        return {"choices": [{"message": {"content": "SUMMARY OK"}}], "usage": {}}

    async def fake_get_ctx(_model):
        return 4096

    monkeypatch.setattr(rs, '_request_summary_completion', fake_request)
    monkeypatch.setattr(rs, 'get_ctx_len_for_model', fake_get_ctx)

    msgs = [{"role": "user", "content": "A" * 12000}, {"role": "assistant", "content": "B" * 12000}]
    out = await rs.summarize_middle(msgs, req_id='r2', summary_model='sum-model')
    assert out == 'SUMMARY OK'
    assert len(calls) >= 2


def test_cache_append_preserves_tail_window(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, 'SUMMARY_CACHE_DIR', str(tmp_path / 'summary_cache3'))

    messages = [
        {"role": "system", "content": "SYSTEM RULES"},
        {"role": "user", "content": "FOUNDATIONAL USER PROMPT"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a5"},
        {"role": "user", "content": "latest question"},
    ]

    from keeprollming.summary_cache import make_cache_entry, save_cache_entry, conversation_fingerprint
    _sys, non_system = app_mod.split_messages(messages)
    fp = conversation_fingerprint(messages=messages, n_head=1)
    entry = make_cache_entry(
        fingerprint=fp,
        start_idx=1,
        end_idx=len(non_system) - 2,
        messages=non_system,
        summary_text='COMPACT',
        summary_model='sum-model',
        token_estimate=10,
        source_mode='cache_append_initial',
    )
    save_cache_entry(str(tmp_path / 'summary_cache3'), entry)

    resp = client.post('/v1/chat/completions', json={"model": "local/main", "messages": messages})
    assert resp.status_code == 200, resp.text
    fake = _get_fake_upstream()
    sent = fake.last_post_json['messages']
    joined = json.dumps(sent, ensure_ascii=False)
    assert 'u4' in joined
    assert 'a5' in joined
    assert 'latest question' in joined


def test_repacked_messages_are_flattened_for_upstream(client):
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
    ]
    resp = client.post('/v1/chat/completions', json={"model": "local/main", "messages": messages})
    assert resp.status_code == 200, resp.text
    fake = _get_fake_upstream()
    sent = fake.last_post_json['messages']
    assert isinstance(sent[-1]['content'], str)
    assert sent[-1]['content'] == 'hello'
