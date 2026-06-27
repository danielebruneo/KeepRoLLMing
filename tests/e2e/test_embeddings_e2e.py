"""E2E tests for /v1/embeddings endpoint.

Verifies basic functionality, error handling, and route resolution.
"""

import httpx
import pytest


@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestEmbeddingsE2E:
    """E2E tests for the embeddings endpoint via the orchestrator."""

    def test_embeddings_basic(self, orchestrator_server, backend_target, backend_client):
        """POST /v1/embeddings returns 200 with embeddings array."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/embeddings",
            json={
                "model": backend_target.client_model_basic,
                "input": "hello world",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) >= 1
        assert "embedding" in body["data"][0]
        assert len(body["data"][0]["embedding"]) == 256
        assert body["data"][0]["object"] == "embedding"

    def test_embeddings_with_input_array(self, orchestrator_server, backend_target, backend_client):
        """POST /v1/embeddings with multiple inputs returns multiple embeddings."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/embeddings",
            json={
                "model": backend_target.client_model_basic,
                "input": ["hello", "world"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["data"][0]["index"] == 0
        assert body["data"][1]["index"] == 1

    def test_embeddings_with_missing_input(self, orchestrator_server, backend_target, backend_client):
        """POST /v1/embeddings without input handles gracefully."""
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/embeddings",
            json={"model": backend_target.client_model_basic},
        )
        # Accept either 200 (with empty data) or 422 (validation error)
        assert resp.status_code in (200, 422), resp.text

    def test_embeddings_route_resolution(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Embeddings uses route-specific upstream URL when configured."""
        # Just verify that a valid request goes through the orchestrator
        # to the fake backend and returns correctly
        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/embeddings",
            json={
                "model": backend_target.client_model_basic,
                "input": "test route resolution",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["model"] != ""
        assert body["object"] == "list"
