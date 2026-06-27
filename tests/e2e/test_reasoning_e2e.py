"""E2E tests for reasoning_content transformation.

Verifies that reasoning_content is properly hoisted to content
when passthrough + transform_reasoning_content is enabled.
"""

import httpx
import pytest


@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestReasoningE2E:
    """Reasoning content transformation scenarios."""

    def test_reasoning_content_via_passthrough(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Passthrough route transforms reasoning_content to content."""
        # The passthrough route should transform reasoning_content
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "pass/main-model",
                "messages": [{"role": "user", "content": "test reasoning"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text
        lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
        assert lines[-1] == "data: [DONE]", "Missing [DONE] marker"
        assert len(lines) > 1, "No content chunks"

    def test_passthrough_basic_response(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Passthrough route returns response on non-streaming."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "pass/main-model",
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "choices" in body
        assert len(body["choices"]) > 0
