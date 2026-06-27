"""Test that SSE streaming responses show assistant text in BASIC_PLAIN mode."""

import json as JSON  # Import with alias to avoid conflict
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


class _CustomFakeAsyncClient:
    """Extended fake client that supports streaming with custom content."""
    
    def __init__(self) -> None:
        self.last_post_url: Optional[str] = None
        self.last_post_json: Optional[Dict[str, Any]] = None
        self.last_stream_url: Optional[str] = None
        self.last_stream_json: Optional[Dict[str, Any]] = None

    async def post(self, url: str, json: Dict[str, Any] = None, headers: Dict[str, Any] = None) -> _FakeResponse:
        self.last_post_url = url
        self.last_post_json = json
        
        # For streaming requests (stream=True), return custom SSE response  
        if json and json.get("stream"):
            chunks = [
                ("data: " + JSON.dumps({
                    "id": "x",
                    "object": "chat.completion.chunk", 
                    "created": 0,
                    "model": str(json.get("model", "test")),
                    "choices": [{"index": 0, "delta": {"content": "Hello world!"}, "finish_reason": None}]
                }) + "\n\n").encode("utf-8"),
                b"data: [DONE]\n\n",
            ]
            return _FakeStreamResponse(chunks)
        
        # For non-streaming requests, return standard response
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

    def stream(self, method: str, url: str, json: Dict[str, Any] = None, headers: Dict[str, Any] = None, payload: Dict[str, Any] = None) -> _FakeStreamCtx:
        assert method == "POST"
        body = json if json is not None else payload
        self.last_stream_url = url
        self.last_stream_json = body
        
        # For streaming requests with stream=True, return full SSE chunks like in post()
        if body and body.get("stream"):
            chunks = [
                ("data: " + JSON.dumps({
                    "id": "x",
                    "object": "chat.completion.chunk", 
                    "created": 0,
                    "model": str(body.get("model", "test")),
                    "choices": [{"index": 0, "delta": {"content": "Hello world!"}, "finish_reason": None}]
                }) + "\n\n").encode("utf-8"),
                b"data: [DONE]\n\n",
            ]
        else:
            # Default fallback for non-streaming or other cases
            chunks = [
                ("data: " + JSON.dumps({
                    "id": "x",
                    "object": "chat.completion.chunk", 
                    "created": 0,
                    "model": str(body.get("model", "m")),
                    "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]
                }) + "\n\n").encode("utf-8"),
                b"data: [DONE]\n\n",
            ]
        return _FakeStreamCtx(_FakeStreamResponse(chunks))


def _get_fake_upstream(app_module) -> _CustomFakeAsyncClient:
    # Stored on module during fixture setup
    return getattr(app_module, "_TEST_FAKE_UPSTREAM")


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """Create test client with fake upstream that supports SSE streaming."""
    # Ensure the app doesn't try to talk to a real upstream.
    fake = _CustomFakeAsyncClient()

    async def _fake_http_client(request_timeout: float):  # Added request_timeout parameter
        return fake

    monkeypatch.setattr(app_mod, "http_client", _fake_http_client)

    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_DIR", str(tmp_path / "summary_cache"))
    monkeypatch.setattr(app_mod, "SUMMARY_MODE", "cache_append")
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_ENABLED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_CONSOLIDATE_WHEN_NEEDED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_FORCE_CONSOLIDATE", False)
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_FINGERPRINT_MSGS", 1)

    # Expose fake client to tests
    monkeypatch.setattr(app_mod, "_TEST_FAKE_UPSTREAM", fake, raising=False)


