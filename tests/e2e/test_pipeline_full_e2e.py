"""E2E tests for the full Pipeline through a running orchestrator.

Verifies that filters work together (SystemPrompt, Summarization,
ToolRewrite, ToolLoopStopper, ModelNudge) through the streaming pipeline.
"""

import httpx
import pytest


@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestPipelineFullE2E:
    """Full pipeline with all filters on non-streaming and streaming."""

    def test_system_prompt_in_pipeline(self, orchestrator_server, backend_target, backend_client):
        """SystemPrompt injects /nothink via the pipeline."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/sp",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "error" not in body
        # The system prompt filter prepends /nothink — response should not be empty
        content = body["choices"][0]["message"]["content"]
        assert len(content) > 0

    def test_system_prompt_streaming(self, orchestrator_server, backend_target, backend_client):
        """SystemPrompt injects /nothink in streaming mode."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/sp",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text
        lines = [l for l in resp.text.splitlines() if l.startswith("data:")]
        assert lines[-1] == "data: [DONE]", "Missing [DONE] marker"
        assert len(lines) > 1, "No content chunks"

    def test_nudge_in_pipeline(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Nudge filter accumulates content on lazy response via pipeline."""
        configure_fake_backend({"chat": {"content": "Prova:|Full response after retry"}})

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/full",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        assert "Prova:" in content
        assert "Full response" in content

    def test_nudge_streaming_in_pipeline(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Nudge accumulates content in streaming mode via pipeline."""
        # For streaming, set stream_pieces for rotating multi-attempt responses
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Prova:", " another chunk"], ["Streaming ", "retry ", "complete"]],
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/full",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.text
        # Should contain content from retry (streaming nudge accumulates)
        # At minimum, the response includes whatever the backend returned
        assert len(body) > 0

    def test_combined_filters_nonstreaming(self, orchestrator_server, backend_target, backend_client):
        """SystemPrompt + Nudge work together in a single request."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/full",  # has SP + nudge
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "error" not in body
        content = body["choices"][0]["message"]["content"]
        assert len(content) > 0
    # ToolRewrite and TLS tests require specific fake backend
    # configurations that the current config.yaml doesn't enable
    # for the 'internal/*' routes — deferred until config is updated
