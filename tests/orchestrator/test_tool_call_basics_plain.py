"""
Test for tool call logging in BASIC_PLAIN mode.

Simulates a real agent flow with multiple rounds of:
1. User message
2. Assistant makes tool call (streaming)
3. Tool result (from conversation history / next round)
4. Assistant continues with final response

This test verifies that in BASIC_PLAIN mode we see:
- CALL markers for tool calls
- TOOL_RESULT markers for tool responses
"""

import json as JSON
import pytest
from fastapi.testclient import TestClient

import keeprollming.app as app_mod
import keeprollming.logger as logger_mod


class _FakeStreamCtx:
    """Mock stream context manager."""
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        pass


class _FakeStreamResponse:
    """Mock stream response for streaming endpoints."""
    def __init__(self, chunks):
        self._chunks = chunks
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


# Global fake client that will be patched into the system
_fake_client_instance = None


class AgentFlowClient:
    """
    Fake client that simulates a real agent loop with tool calls.
    
    Simulates a conversation like:
      User: "Check /tmp and calculate 2+2"  
      Assistant: [tool_call: run_command("/tmp")] → result → [tool_call: compute(2+2)] → result → final answer
    """
    def __init__(self):
        self.requests = []

    async def post(self, url, json=None, headers=None):
        """Non-streaming POST."""
        self.requests.append({"method": "POST", "url": url, "json": json})
        # Return a proper non-streaming response object
        import json as _json
        class _FakeResp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {"total_tokens": 5}}
            async def text(self):
                return _json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        return _FakeResp()

    def stream(self, method=None, url=None, json=None, headers=None, **kwargs):
        """Streaming — simulates a full agent round-trip with tool calls."""
        self.requests.append({"method": "stream", "json": json})
        body = json or kwargs.get("payload") or {}

        # Simulate: assistant calls two tools sequentially in one stream
        chunks = [
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',

            # Tool call 1 starts
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_run123","type":"function","function":{"name":"run_command","arguments":""}}]},"finish_reason":null}]}\n\n',

            # Tool call 1: arguments arriving
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"/tmp"}}]},"finish_reason":null}]}\n\n',

            # Tool call 1: done
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":""}}]},"finish_reason":null}]}\n\n',

            # Some text between tool calls
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"content":"Checking /tmp... "},"finish_reason":null}]}\n\n',

            # Tool call 2 starts
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"call_calc456","type":"function","function":{"name":"calculate","arguments":""}}]},"finish_reason":null}]}\n\n',

            # Tool call 2: arguments
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"2+2"}}]},"finish_reason":null}]}\n\n',

            # Tool call 2: done
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":""}}]},"finish_reason":null}]}\n\n',

            # Final text
            b'data: {"id":"chat-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"content":"Done."},"finish_reason":"stop"}]}\n\n',

            b'data: [DONE]\n\n'
        ]

        class AgentStreamCtx:
            def __init__(self):
                self._resp = _FakeStreamResponse(chunks)
                self._closed = False

            async def __aenter__(self):
                return self._resp

            async def __aexit__(self, *args):
                self._closed = True

        return AgentStreamCtx()


@pytest.fixture(autouse=True)
def setup_agent_flow(monkeypatch, tmp_path):
    """Setup agent flow client with BASIC_PLAIN logging."""
    global _fake_client_instance
    _fake_client_instance = AgentFlowClient()

    # Setup BASIC_PLAIN mode
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_SNIP_CHARS", 2000)
    monkeypatch.setattr(logger_mod, "BASIC_SNIP_CHARS", 0)
    from keeprollming.logging import constants as logging_constants
    monkeypatch.setattr(logging_constants, "LOG_PLAIN_COLORS", False)

    # Patch LOG_MODE in endpoint modules (they use _logger.LOG_MODE now)
    from keeprollming.endpoints import chat_completions as cc_mod
    monkeypatch.setattr(cc_mod._logger, "LOG_MODE", "BASIC_PLAIN")

    from keeprollming.endpoints import streaming_handlers as sh_mod
    monkeypatch.setattr(sh_mod._logger, "LOG_MODE", "BASIC_PLAIN")

     # Mock upstream http_client — must match signature used by chat_completions.py
    async def _fake_http_client(request_timeout):
        return _fake_client_instance

    from keeprollming import upstream
    monkeypatch.setattr(upstream, "http_client", _fake_http_client)
    
    # Also patch where it's imported in chat_completions module
    from keeprollming.endpoints import chat_completions as cc_mod
    monkeypatch.setattr(cc_mod, "http_client", _fake_http_client)

    # Summary cache settings
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_DIR", str(tmp_path / "summary_cache"))
    monkeypatch.setattr(app_mod, "SUMMARY_MODE", "cache_append")
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_ENABLED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_CONSOLIDATE_WHEN_NEEDED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_FORCE_CONSOLIDATE", False)

    from keeprollming.app import app
    yield app


@pytest.fixture
def client(setup_agent_flow):
    """Test client."""
    return TestClient(setup_agent_flow, raise_server_exceptions=True)