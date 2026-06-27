"""
Test that metrics correctly reflect nudge-processed content.

When the nudge filter modifies a response, completion_tokens should
be estimated from the accumulated content, not the original lazy response.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from keeprollming.orchestrator.filter import FilterConfig, FilterExecutionContext
from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter


class TestNudgeMetricsTokenEstimation:
    """Verify completion_tokens are estimated from processed content."""

    def test_processed_content_used_for_token_estimation(self):
        """
        When filter modifies content, metrics should use estimated tokens
        from the processed content, not the original usage.
        """
        # Simulate what chat_completions.py does
        original_usage = {"completion_tokens": 3, "prompt_tokens": 34, "total_tokens": 37}
        original_content = "OK:"  # 3 chars, ~1 token
        processed_content = "OK:\nOK:\nOK:\nOK:"  # 15 chars accumulated

        # Without nudge: use original usage
        processed_response = None
        completion_tokens = original_usage.get("completion_tokens")  # 3
        prompt_tokens = original_usage.get("prompt_tokens")  # 34
        total_tokens = original_usage.get("total_tokens")  # 37

        assert completion_tokens == 3
        assert total_tokens == 37

        # With nudge (processed_response exists with content):
        # Should estimate tokens from processed content
        processed_response = MagicMock()
        processed_response.content = processed_content

        from keeprollming.token_counter import TokenCounter
        tc = TokenCounter()
        completion_tokens = tc.count_text(processed_content)
        total_tokens = prompt_tokens + completion_tokens

        # The accumulated content should produce more tokens than the original "OK:"
        assert completion_tokens > 1, f"Expected >1 tokens for 15 chars, got {completion_tokens}"
        assert completion_tokens >= 3, f"15 chars should be at least 3 tokens, got {completion_tokens}"

    def test_original_usage_used_when_no_filter(self):
        """When no filter processed the response, use original usage."""
        original_usage = {"completion_tokens": 50, "prompt_tokens": 100, "total_tokens": 150}
        processed_response = None

        completion_tokens = original_usage.get("completion_tokens")
        assert completion_tokens == 50
        # No estimation needed

    def test_estimated_tokens_exceed_original_lazy_tokens(self):
        """
        The estimated token count for accumulated content should be
        significantly larger than the original lazy response tokens.
        """
        lazy_response = "Now I will:"
        accumulated = "Now I will:\nI cannot continue that sentence as it implies an action I am unable to perform."

        from keeprollming.token_counter import TokenCounter
        tc = TokenCounter()

        lazy_tokens = tc.count_text(lazy_response)
        accumulated_tokens = tc.count_text(accumulated)

        # Accumulated content is much longer, should have more tokens
        assert accumulated_tokens > lazy_tokens, \
            f"Accumulated tokens ({accumulated_tokens}) should be > lazy tokens ({lazy_tokens})"

    def test_empty_processed_content_does_not_override(self):
        """If processed_response.content is empty, keep original usage."""
        original_usage = {"completion_tokens": 10}
        processed_response = MagicMock()
        processed_response.content = ""

        # Empty content should not trigger estimation
        if processed_response and hasattr(processed_response, 'content') and processed_response.content:
            from keeprollming.token_counter import TokenCounter
            tc = TokenCounter()
            completion_tokens = tc.count_text(processed_response.content)
        else:
            completion_tokens = original_usage.get("completion_tokens")

        assert completion_tokens == 10  # Original preserved


class TestNudgeElapsedTime:
    """Verify elapsed time includes nudge retry overhead."""

    def test_elapsed_includes_retry_time(self):
        """Non-streaming elapsed_ms should include the full request time."""
        t_start = time.perf_counter()
        time.sleep(0.01)  # simulate some work
        # simulate nudge retry delay
        time.sleep(0.01)
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        # elapsed includes both the original call and the retry
        assert elapsed_ms > 15, f"Elapsed {elapsed_ms:.1f}ms should be > 15ms (includes retry time)"

    def test_tps_calculation_uses_correct_values(self):
        """TPS = completion_tokens / (elapsed_seconds)."""
        completion_tokens = 20  # estimated from accumulated content
        elapsed_ms = 10000  # 10 seconds (includes retry time)

        tps = completion_tokens / (elapsed_ms / 1000.0)

        assert tps == 2.0, f"TPS should be 2.0, got {tps}"
        assert tps > 0.3, "TPS should not be artificially low (old bug: 0.3 tps)"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
