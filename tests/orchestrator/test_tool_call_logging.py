"""
Test for tool call flow with BASIC_PLAIN logging.

This test verifies that:
1. Tool calls are clearly visible in BASIC_PLAIN logs
2. Tool responses/results are shown when agent receives them
3. Subsequent assistant turns are logged correctly
"""

import json as JSON  # Import with alias to avoid conflict
from typing import Any, AsyncIterator, Dict, Optional
import pytest
from fastapi.testclient import TestClient

import keeprollming.app as app_mod
import keeprollming.logger as logger_mod


@pytest.fixture
def tool_call_tracking_client():
    """Create a fake client that tracks tool call sequence."""
    import sys
    from pathlib import Path

    # Add tests directory to path if not already present
    tests_dir = str(Path(__file__).parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)

    from conftest import _FakeStreamCtx, _FakeStreamResponse
    
    class ToolCallTrackingClient:
        """Custom fake client that tracks tool call sequence."""

        def __init__(self):
            self.requests = []
            self.response_sequence_index = 0

        async def post(self, url: str, json: Dict[str, Any] = None, headers: Dict[str, Any] = None) -> _FakeStreamResponse:
            """Handle non-streaming POST requests."""
            self.requests.append({
                "method": "POST",
                "url": url,
                "json": json
            })

            # For streaming requests (stream=True), return SSE response with tool call simulation
            if json and json.get("stream"):
                stream_chunks = [
                    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',
                    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"content":"I need to <execute>"}}]}\n\n',
                    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"content":" <exec>ls -la</exec>","tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"run_command","arguments":"{\\"command\\":\\"ls -la\\"}"}}]}}}\n\n',
                    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"content":" The files are"},"finish_reason":null}]}\n\n',
                    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"content":", I see."},"finish_reason":"stop"}]}\n\n',
                    b'data: [DONE]\n\n'
                ]
                return _FakeStreamResponse(stream_chunks)

            # For non-streaming requests, return standard response
            return _FakeStreamResponse(
                status_code=200,
                json_data={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": json.get("model", "unknown") if json else "unknown",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Final response"
                            },
                            "finish_reason": "stop"
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
                }
            )

        def stream(self, method: str = None, url: str = None, json: Dict[str, Any] = None, headers: Dict[str, Any] = None, payload: Dict[str, Any] = None) -> _FakeStreamCtx:
            """Handle streaming requests with tool call simulation."""
            self.requests.append({
                "method": "stream",
                "url": url,
                "json": json
            })

            body = json if json is not None else payload

            # Simulate a realistic flow:
            # 1. Initial assistant response mentioning tool use
            # 2. Tool call in XML format
            # 3. Tool result in XML format
            # 4. Final assistant response with tool result

            stream_chunks = [
                # Chunk 1: Start of response
                b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n',

                # Chunk 2: Some text before tool call
                b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"content":"I need to check"},"finish_reason":null}]}\n\n',

                # Chunk 3: Tool call starts (structured tool_calls delta)
                b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"run_command","arguments":""}}]},"finish_reason":null}]}\n\n',

                # Chunk 4: Tool call arguments
                b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ls -la"}}]},"finish_reason":null}]}\n\n',

                # Chunk 5: More text after tool call
                b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"content":" The files are"},"finish_reason":null}]}\n\n',

                # Chunk 6: Final response
                b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"test","choices":[{"index":0,"delta":{"content":", I see."},"finish_reason":"stop"}]}\n\n',

                # Done marker
                b'data: [DONE]\n\n'
            ]

            return _FakeStreamCtx(_FakeStreamResponse(stream_chunks))
    
    return ToolCallTrackingClient()


@pytest.fixture
def client(monkeypatch, tmp_path, tool_call_tracking_client) -> TestClient:
    """Create test client with mocked upstream."""
    # Setup logging for BASIC_PLAIN mode
    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logger_mod, "LOG_SNIP_CHARS", 2000)
    monkeypatch.setattr(logger_mod, "BASIC_SNIP_CHARS", 0)
    from keeprollming.logging import constants as logging_constants
    monkeypatch.setattr(logging_constants, "LOG_PLAIN_COLORS", False)

    # Patch LOG_MODE in streaming_handlers module (it now uses logger.LOG_MODE)
    from keeprollming.endpoints import streaming_handlers as sh_mod
    monkeypatch.setattr(sh_mod._logger, "LOG_MODE", "BASIC_PLAIN")

    # Create fake client that mimics streaming behavior like test_sse_basic_plain_logs.py
    fake_client = tool_call_tracking_client

    async def _fake_http_client(request_timeout: float):  # Must match signature
        return fake_client

    from keeprollming import upstream
    monkeypatch.setattr(upstream, "http_client", _fake_http_client)

    # Also patch where it's used in endpoint modules (they import http_client directly)
    from keeprollming.endpoints import chat_completions as cc_mod
    monkeypatch.setattr(cc_mod, "http_client", _fake_http_client)

    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_DIR", str(tmp_path / "summary_cache"))
    monkeypatch.setattr(app_mod, "SUMMARY_MODE", "cache_append")
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_ENABLED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_CONSOLIDATE_WHEN_NEEDED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_FORCE_CONSOLIDATE", False)

    from keeprollming.app import app
    return TestClient(app, raise_server_exceptions=True)