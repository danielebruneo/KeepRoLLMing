"""
Test suite for custom summary prompts functionality
"""

import pytest
from starlette.testclient import TestClient
from keeprollming.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_custom_prompt_in_payload(client):
    """Test that custom prompt is parsed from request payload"""
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "test message"}
        ],
        "summary_prompt": "This is a custom prompt"
    })
    
    # Should not fail with parsing errors
    assert response.status_code == 200


def test_custom_prompt_with_type(client):
    """Test that custom prompt works when both type and text are provided"""
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "test message"}
        ],
        "summary_prompt_type": "custom",
        "summary_prompt": "This is a custom prompt"
    })
    
    # Should not fail with parsing errors
    assert response.status_code == 200


def test_default_behavior_without_custom_prompt(client):
    """Test that default behavior works when no custom prompt is provided"""
    response = client.post("/v1/chat/completions", json={
        "model": "local/quick",
        "messages": [
            {"role": "user", "content": "test message"}
        ]
    })
    
    # Should not fail with parsing errors
    assert response.status_code == 200


def test_passthrough_with_custom_prompt(client):
    """Test that passthrough mode works with custom prompt"""
    response = client.post("/v1/chat/completions", json={
        "model": "pass/test-model",
        "messages": [
            {"role": "user", "content": "test message"}
        ],
        "summary_prompt": "This is a custom prompt"
    })
    
    # Should not fail with parsing errors
    assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])