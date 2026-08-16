"""Pytest configuration for Keeprollmg LLM Orchestrator tests."""

import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

import pytest
from fastapi.testclient import TestClient


# Markers are registered in pytest.ini:
# - e2e_fake: E2E tests with fake backend
# - e2e_live: E2E tests requiring live API calls (skipped if no API key)
# - non_parallelizable: Tests that cannot run in parallel due to shared resources


# Stream context and response fixtures for tool call tests
class _FakeStreamResponse:
    """Mock stream response for testing streaming endpoints."""

    def __init__(self, chunks: list[bytes] = None, status_code: int = 200) -> None:
        self._chunks = chunks or []
        self.status_code = status_code
        self.headers = {"content-type": "text/event-stream"}
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeStreamCtx:
    """Mock stream context manager for testing."""

    def __init__(self, resp: "_FakeStreamResponse") -> None:
        self._resp = resp

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self._resp

    async def __aexit__(self, *args) -> None:
        pass

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._resp.aiter_bytes()


class OpenAIToolCallClient:
    """Mock client that simulates OpenAI's native tool call format (function_calls)."""

    def __init__(self):
        self.last_request: Optional[Dict[str, Any]] = None

    async def post(self, url: str, json: Dict[str, Any] = None, headers: Dict[str, Any] = None) -> "_FakeResponse":
        """Handle non-streaming POST requests with OpenAI tool call format."""
        self.last_request = json
        return _FakeResponse({
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc123",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"location": "Milan", "unit": "celsius"}'
                                }
                            }
                        ]
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
        })

    def stream(self, method: str = None, url: str = None, json: Dict[str, Any] = None, headers: Dict[str, Any] = None, **kwargs):
        """Mock streaming for tool call responses."""
        body = json or kwargs.get("payload") or {}

        class OpenAIToolStream:
            def __init__(self, request_body: Dict[str, Any]):
                self.request = request_body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                import json as json_module
                # Stream a tool call in OpenAI format
                chunks = [
                    b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n',
                    b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"get_weather","arguments":""},"type":"function"}]},"finish_reason":null}]}\n\n',
                    b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"location\\\\": \\\\"Milan\\"}"}}],"type":"function"}],"finish_reason":null}]}\n\n',
                    b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\", \\"unit\\\\": \\\\"celsius\\"}"}}],"type":"function"}],"finish_reason":null}]}\n\n',
                    b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":""}}],"type":"function"}],"finish_reason":"stop"}]}\n\n',
                    b'data: [DONE]\n\n'
                ]
                async def gen():
                    for chunk in chunks:
                        yield chunk
                return gen()

        return OpenAIToolStream(body or {})


# Set environment variable to use test config before any imports
TEST_CONFIG_PATH = Path(__file__).parent / "config.test.yaml"
os.environ["CONFIG_FILE"] = str(TEST_CONFIG_PATH)

# Ensure project root is importable when pytest chooses a different rootdir.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeAsyncClient:
    """Mock HTTP client for testing."""

    def __init__(self):
        self.last_request: Optional[Dict[str, Any]] = None
        self.last_post_json: Optional[Dict[str, Any]] = None


class _FakeResponse:
    """Mock aiohttp response object."""

    def __init__(self, data: Dict[str, Any]):
        import json
        self.status_code = 200
        self._data = data
        self._json_data = json.dumps(data)
        self.content = self._json_data.encode('utf-8')  # aiohttp uses bytes for .content

    async def text(self):
        return self._json_data

    def json(self):
        """Synchronous json() method for compatibility."""
        import json
        return json.loads(self._json_data)

    def raise_for_status(self):
        """Raise an exception if status code indicates error."""
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _FakeClientSession:
    """Mock aiohttp session."""

    def __init__(self, fake_client: "_FakeAsyncClient"):
        self._fake_client = fake_client

    async def post(self, url: str, **kwargs) -> "_FakeResponse":
        data = kwargs.get('json') or kwargs.get('data')

        if isinstance(data, dict):
            self._fake_client.last_request = data.copy()
            self._fake_client.last_post_json = data.copy()

        return _FakeResponse({
            "choices": [{"message": {"content": "mock_response"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10}
        })

    async def get(self, url: str, **kwargs) -> "_FakeResponse":
        return _FakeResponse({"status": "ok"})

    def stream(self, method: str, url: str, **kwargs):
        """Mock stream context manager for streaming requests."""
        # Return a mock context manager that yields mock chunks
        class MockStream:
            def __init__(self):
                self._closed = False
                self.status_code = 200
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                self._closed = True
            
            def __aiter__(self):
                # Return empty iterator for now - tests may override this
                async def gen():
                    if False:  # Never yields
                        yield b""
                return gen()

            async def aiter_bytes(self):
                import json
                evt = {
                    "id": "x",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "test-quick-model",
                    "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
                }
                yield b"data: " + json.dumps(evt).encode("utf-8") + b"\n\n"
                yield b"data: [DONE]\n\n"
        
        return MockStream()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# Set up patches BEFORE any test imports happen
def _setup_patches():
    """Set up all necessary patches at module import time."""
    import keeprollming.upstream as upstream_mod
    from keeprollming.endpoints import chat_completions
    import keeprollming.summary as summary_mod

    fake_client = _FakeAsyncClient()

    async def _fake_http_client(request_timeout: float = 120.0):
        return _FakeClientSession(fake_client)

    # Patch http_client where it's used in summary package (imports from upstream)
    summary_mod.http_client = _fake_http_client
    
    # Also patch in endpoints.chat_completions module
    chat_completions.http_client = _fake_http_client

    # Also patch in upstream module for other uses
    upstream_mod.http_client = _fake_http_client

    # Store fake client for tests to access
    import keeprollming.app as app_mod
    app_mod._TEST_FAKE_UPSTREAM = fake_client

    return fake_client


# Create global fake client at module load time
_fake_upstream = _setup_patches()


@pytest.fixture(autouse=True)
def setup_keeprollming_modules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Setup keeprollming modules with proper initialization and patching."""
    from tests.orchestrator.test_module_patches import patch_app_mod_functions
    
    # Import the package fresh to get clean state
    import keeprollming.app as app_mod

    # Apply modular patching helper to ensure patches propagate correctly
    # This ensures that when tests mock functions, they work with the modular code
    def apply_modular_patching(func_patches):
        """Helper to apply patches at all necessary locations for modular code."""
        patch_app_mod_functions(monkeypatch, func_patches)

    yield
    
    # Cleanup if needed


def pytest_configure(config):
    """Configure pytest with proper module initialization."""


# Global state for mock summary
_USE_MOCK_SUMMARY = False
_FAKE_SUMMARY_RETURN_VALUE = "MOCK_SUMMARY"


async def _fake_summarize_middle(middle, req_id, summary_model, **kwargs):
    """Mock summarize_middle for tests."""
    global _USE_MOCK_SUMMARY
    if _USE_MOCK_SUMMARY:
        return _FAKE_SUMMARY_RETURN_VALUE
    # Fall through to real implementation
    from keeprollming.summary import summarize_middle as original_summarize_middle
    return await original_summarize_middle(middle, req_id, summary_model, **kwargs)


def set_mock_summary(enabled: bool, return_value: str = "MOCK_SUMMARY"):
    """Enable or disable mock summary and set return value."""
    global _USE_MOCK_SUMMARY, _FAKE_SUMMARY_RETURN_VALUE
    _USE_MOCK_SUMMARY = enabled
    _FAKE_SUMMARY_RETURN_VALUE = return_value


# Global fake upstream client for testing
_FAKE_UPSTREAM_CLIENT = None


@pytest.fixture(autouse=True)
def reset_mock_summary():
    """Reset mock summary state before each test."""
    global _USE_MOCK_SUMMARY, _FAKE_UPSTREAM_CLIENT
    _USE_MOCK_SUMMARY = False
    _FAKE_UPSTREAM_CLIENT = None
    yield


@pytest.fixture
def client(monkeypatch):
    """Create a test client for API tests.

    This fixture creates a FastAPI TestClient that can be used to test
    the application's HTTP endpoints in isolation.
    """
    from keeprollming import app as app_mod
    
    # Set up mock upstream before creating client
    _setup_fake_upstream(monkeypatch)
    
    return TestClient(app_mod.app, raise_server_exceptions=False)


def get_fake_upstream():
    """Get the global fake upstream client.

    This is used by tests to inspect what was sent to the upstream.
    """
    global _FAKE_UPSTREAM_CLIENT
    if _FAKE_UPSTREAM_CLIENT is None:
        raise RuntimeError("_setup_fake_upstream must be called first via fixture")
    return _FAKE_UPSTREAM_CLIENT


def _setup_fake_upstream(monkeypatch):
    """Set up a fake upstream for testing.

    This monkeypatches the HTTP client to return mock responses instead
    of making real requests, while capturing what was sent.
    """
    import httpx
    
    global _FAKE_UPSTREAM_CLIENT
    
    class FakeHTTPClient:
        def __init__(self):
            self.last_post_json = None
        
        def post(self, *args, **kwargs):
            # Capture the request body
            if 'json' in kwargs:
                self.last_post_json = kwargs['json']
            
            return httpx.Response(
                status_code=200,
                json={"choices": [{"message": {"content": "mock_response"}}]}
            )
    
    _FAKE_UPSTREAM_CLIENT = FakeHTTPClient()
    
    # Store original __init__ and call it with all arguments
    original_init = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, "__init__", lambda self, *args, **kwargs: original_init(self, *args, **kwargs))
    monkeypatch.setattr(httpx.Client, "post", _FAKE_UPSTREAM_CLIENT.post)
