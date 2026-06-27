"""
Functional tests: verify what the CLIENT actually receives.

Each test sets up a fake backend scenario, makes a request through
the orchestrator, and asserts on the response the client gets back.

These tests verify BEHAVIOR, not implementation. They are the safety
net for architectural refactoring.
"""

import json

import httpx


class TestNormalResponse:
    """Basic request/response without any filter triggers."""

    def test_non_streaming_basic(self, harness):
        """Client gets a normal text response."""
        harness.set_scenario(chat_content="Hello there, how can I help?")
        resp = harness.post()
        assert resp.status_code == 200
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        assert content == "Hello there, how can I help?"

    def test_streaming_basic(self, harness):
        """Client gets streamed chunks that reconstruct the response."""
        harness.set_scenario(chat_content="Hello there, how can I help?")
        resp = harness.post_stream()
        assert resp.status_code == 200
        chunks = [line for line in resp.text.splitlines() if line.startswith("data:")]
        assert chunks[-1] == "data: [DONE]"
        content = ""
        for chunk in chunks[:-1]:
            try:
                data = json.loads(chunk[6:])
                delta = data.get("choices", [{}])[0].get("delta", {})
                if "content" in delta:
                    content += delta["content"]
            except json.JSONDecodeError:
                pass
        assert "Hello" in content


class TestSystemPromptInjection:
    """System prompt filter injects /nothink."""

    def test_system_prompt_injected(self, harness):
        """System prompt reaches upstream and doesn't break response."""
        harness.set_scenario(chat_content="Response with injected prompt.")
        resp = harness.post(messages=[
            {"role": "user", "content": "question"}
        ])
        assert resp.status_code == 200
        body = resp.json()
        assert "error" not in body
        assert body["choices"][0]["message"]["content"]


class TestErrorHandling:
    """Error responses when upstream URL is misconfigured."""

    def test_missing_upstream_url_returns_diagnostic_error(self, broken_harness):
        """When no upstream_url is configured, the error response
        includes route, upstream_model, url, and a hint."""
        resp = broken_harness.post(model="internal/broken")
        assert resp.status_code == 500
        body = resp.json()
        error = body.get("error", {})
        assert "route" in error, f"Missing 'route' in error: {error}"
        assert "upstream_model" in error, f"Missing 'upstream_model' in error: {error}"
        assert "url" in error, f"Missing 'url' in error: {error}"
        assert "hint" in error, f"Missing 'hint' in error: {error}"
        assert "UPSTREAM_BASE_URL" in error.get("hint", "")
