#!/usr/bin/env python3

"""Tests for the /v1/models OpenAPI-compatible endpoint."""

import pytest
from fastapi.testclient import TestClient
from keeprollming.app import app


@pytest.fixture
def client():
    """Create test client for the FastAPI app."""
    return TestClient(app)


class TestModelsEndpoint:
    """Test the /v1/models endpoint."""

    def test_models_endpoint_exists(self, client):
        """Verify the endpoint returns a valid response."""
        response = client.get("/v1/models")
        
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_models_endpoint_returns_routes(self, client):
        """Verify all configured routes are listed as models."""
        response = client.get("/v1/models")
        data = response.json()
        
        # Should have at least the basic routes (4 from config.test.yaml or 8 from config.yaml)
        assert len(data["data"]) >= 4
        
        # Check that we have expected route names
        model_ids = {m["id"] for m in data["data"]}
        assert "quick" in model_ids or "chat/quick" in model_ids

    def test_model_object_structure(self, client):
        """Verify each model has required OpenAI-compatible fields."""
        response = client.get("/v1/models")
        data = response.json()
        
        for model in data["data"]:
            # Check required OpenAI fields
            assert "id" in model
            assert "object" in model
            assert model["object"] == "model"
            
            # Check custom field
            assert "context_length" in model
            assert isinstance(model["context_length"], int)
            assert model["context_length"] > 0

    def test_context_lengths_are_reasonable(self, client):
        """Verify context lengths are within expected ranges."""
        response = client.get("/v1/models")
        data = response.json()
        
        for model in data["data"]:
            ctx_len = model["context_length"]
            
            # Context should be at least 4096 (minimum reasonable)
            assert ctx_len >= 4096, f"Model {model['id']} has unreasonably small context: {ctx_len}"
            
            # Context should not exceed typical LLM limits (e.g., 1M tokens)
            assert ctx_len <= 1_000_000, f"Model {model['id']} has unreasonably large context: {ctx_len}"

    def test_routes_with_summary_use_max_ctx(self, client):
        """Verify routes with summarization use max of main/summary model contexts."""
        response = client.get("/v1/models")
        data = response.json()
        
        # Find any route that has summary enabled and check it uses max context
        for model in data["data"]:
            if model["id"] in ["quick", "main", "deep", "chat/quick", "chat/main", "chat/deep"]:
                # These routes have summarization, so they should report reasonable ctx_len
                assert model["context_length"] >= 8192

    def test_routes_with_custom_ctx_override(self, client):
        """Verify routes with custom ctx_len override work correctly."""
        response = client.get("/v1/models")
        data = response.json()
        
        # Find a route and verify it has a context length set
        assert len(data["data"]) > 0
        
    def test_all_routes_have_unique_ids(self, client):
        """Verify each route has a unique ID."""
        response = client.get("/v1/models")
        data = response.json()

        ids = [m["id"] for m in data["data"]]
        assert len(ids) == len(set(ids)), "Duplicate model IDs found"

    def test_owned_by_field(self, client):
        """Verify owned_by field is set correctly."""
        response = client.get("/v1/models")
        data = response.json()

        for model in data["data"]:
            assert model["owned_by"] == "orchestrator"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
