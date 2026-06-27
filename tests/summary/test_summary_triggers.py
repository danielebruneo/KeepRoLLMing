"""Tests for summary trigger logic.

These tests verify that summarization is triggered correctly based on message patterns.
Migrated from test_orchestrator.py as part of Phase 2 test refactoring.
"""

import pytest
from fastapi.testclient import TestClient

import keeprollming.app as app_mod


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """Create test client with summary cache settings."""
    # Get the globally created fake client from conftest
    from tests.conftest import _fake_upstream

    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_DIR", str(tmp_path / "summary_cache"))
    monkeypatch.setattr(app_mod, "SUMMARY_MODE", "cache_append")
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_ENABLED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_CONSOLIDATE_WHEN_NEEDED", True)
    monkeypatch.setattr(app_mod, "SUMMARY_FORCE_CONSOLIDATE", False)
    monkeypatch.setattr(app_mod, "SUMMARY_CACHE_FINGERPRINT_MSGS", 1)

    # Expose fake client to tests
    monkeypatch.setattr(app_mod, "_TEST_FAKE_UPSTREAM", _fake_upstream, raising=False)

    return TestClient(app_mod.app)


def test_rolling_summary_trigger_repacked_messages(client, monkeypatch):
    """Test that rolling summary triggers repacked messages correctly."""
    from keeprollming.summary import should_summarise as original_should_summarise

    async def _fake_summary(middle, req_id, summary_model, **kwargs):
        return "SOMMARIO-TEST"

    # Patch where the function is defined (in summary_orchestrator module)
    monkeypatch.setattr("keeprollming.summary.summary_orchestrator.summarize_middle", _fake_summary)

    # Mock should_summarise to always trigger summarization for testing
    def mock_should_summarise(*args, **kwargs):
        plan = original_should_summarise(*args, **kwargs)
        return type(plan)(
            should=True,
            reason="mocked_for_testing",
            threshold=plan.threshold,
            prompt_tok_est=plan.prompt_tok_est,
            head_n=1,
            tail_n=1,
            middle_count=max(0, len(kwargs.get("messages", [])) - 2),
            repacked_tok_est=plan.repacked_tok_est,
            pinned_head_n=plan.pinned_head_n or 0,
        )

    monkeypatch.setattr("keeprollming.summary.should_summarise", mock_should_summarise)

    long_text = "x" * 2000
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/main",
            "messages": [{"role": "user", "content": long_text}, {"role": "assistant", "content": "y" * 2000}],
        },
    )
    assert resp.status_code == 200


def test_web_search_payload_can_still_trigger_summary(client, monkeypatch):
    """Test that web search payloads can still trigger summarization."""
    from keeprollming.summary import should_summarise as original_should_summarise

    async def _fake_summary(middle, req_id, summary_model, **kwargs):
        return "WEB-SUMMARY"

    # Patch where the function is defined (in summary_orchestrator module)
    monkeypatch.setattr("keeprollming.summary.summary_orchestrator.summarize_middle", _fake_summary)

    # Mock should_summarise to trigger summary
    def mock_should_summarise(*args, **kwargs):
        plan = original_should_summarise(*args, **kwargs)
        return type(plan)(
            should=True,
            reason="web_search_payload",
            threshold=plan.threshold,
            prompt_tok_est=plan.prompt_tok_est,
            head_n=1,
            tail_n=1,
            middle_count=max(0, len(kwargs.get("messages", [])) - 2),
            repacked_tok_est=plan.repacked_tok_est,
            pinned_head_n=plan.pinned_head_n or 0,
        )

    monkeypatch.setattr("keeprollming.summary.should_summarise", mock_should_summarise)

    long_text = "x" * 2000
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "local/main",
            "messages": [
                {"role": "user", "content": long_text},
                {"role": "assistant", "content": "y" * 2000},
            ],
        },
    )
    assert resp.status_code == 200


def test_passthrough_model_routes_without_summary(client, monkeypatch):
    """Verify that pass/* model routes completely bypass summarization.

    This is a critical regression test: if summarize_middle gets called for
    passthrough models, it means the routing logic failed to short-circuit
    early enough.
    """
    from keeprollming import app

    # If summarize_middle is called in passthrough mode, the test should fail.
    async def _boom(*args, **kwargs):
        raise AssertionError("summarize_middle should not be called for pass/* models")

    # Patch where the function is actually used (in app module)
    monkeypatch.setattr(app, "summarize_middle", _boom)

    long_text = "x" * 2000
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "pass/my-backend-model",
            "messages": [
                {"role": "user", "content": long_text},
                {"role": "user", "content": long_text}
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    fake = _get_fake_upstream()
    assert fake.last_post_json is not None
    assert fake.last_post_json["model"] == "my-backend-model"


def _get_fake_upstream():
    """Helper to get the global fake upstream client."""
    from tests.conftest import _fake_upstream
    return _fake_upstream
