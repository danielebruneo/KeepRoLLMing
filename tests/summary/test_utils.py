"""Unit tests for summary utility functions."""

import pytest


def test_sanitize_summary_text_removes_prompt_echo():
    """Test that _sanitize_summary_text properly removes prompt echo markers.
    
    This is critical to prevent the summary from including its own instructions
    in the output, which would cause infinite loops or confusion.
    """
    from keeprollming.rolling_summary import _sanitize_summary_text

    raw = """=== EXISTING SUMMARY START ===
[ARCHIVED_COMPACT_CONTEXT]
EXTRACTION_SUMMARY_START
hello
[/EXTRACTION_SUMMARY_START]
[/ARCHIVED_COMPACT_CONTEXT]
=== EXISTING SUMMARY END ===

=== NEW MESSAGES START ===
USER: test
=== NEW MESSAGES END ==="""
    cleaned = _sanitize_summary_text(raw)

    # Should remove all the markers and keep only actual content
    assert "EXISTING SUMMARY START" not in cleaned
    assert "ARCHIVED_COMPACT_CONTEXT" not in cleaned
    assert "EXTRACTION_SUMMARY_START" not in cleaned
    assert "NEW MESSAGES START" not in cleaned
    assert "NEW MESSAGES END" not in cleaned

    # Should preserve actual content (prompt echo markers are removed)
    assert "hello" in cleaned


@pytest.mark.asyncio
async def test_summary_middle_overflow_chunks(monkeypatch):
    """Test that summary properly chunks when context overflow occurs.
    
    Scenario: First summary attempt exceeds backend context limit (4096 tokens).
    Expected behavior: Automatically chunk messages and retry with smaller set.
    """
    import keeprollming.rolling_summary as rs
    from keeprollming.summary import summary_orchestrator

    calls = []

    async def fake_request(_body, timeout=120.0):
        """Mock HTTP request that fails on first attempt (overflow), succeeds on retry."""
        calls.append((_body, timeout))
        if len(calls) == 1:
            # First call: simulate context overflow error
            class DummyResp:
                def json(self):
                    return {
                        "error": {
                            "message": "request exceeds the available context size (4096 tokens), try increasing it",
                            "n_ctx": 4096
                        }
                    }
                text = "request exceeds the available context size"

            import httpx
            raise httpx.HTTPStatusError("overflow", request=None, response=DummyResp())
        # Second call (with reduced context): success
        return {
            "choices": [{"message": {"content": "SUMMARY OK"}}],
            "usage": {}
        }

    async def fake_get_ctx(_model):
        """Mock context length lookup."""
        return 4096

    # Patch at all necessary locations for modular code to work
    monkeypatch.setattr(rs, '_request_summary_completion', fake_request)
    monkeypatch.setattr(summary_orchestrator, '_request_summary_completion', fake_request)
    monkeypatch.setattr(rs, 'get_ctx_len_for_model', fake_get_ctx)

    # Create oversized messages that will trigger chunking
    msgs = [
        {"role": "user", "content": "A" * 12000},
        {"role": "assistant", "content": "B" * 12000}
    ]
    out = await rs.summarize_middle(msgs, req_id='r1', summary_model='sum-model')

    assert out == 'SUMMARY OK'
    # Should have retried at least once with reduced context
    assert len(calls) >= 2
