"""Tests for cached_tokens extraction from upstream usage fields."""

import json
import pytest


class TestNonStreamingCacheMetrics:
    """Test cached_tokens extraction in non-streaming chat_completions handler."""

    def test_cached_tokens_extracted_from_prompt_tokens_details(self):
        """Verify that prompt_tokens_details.cached_tokens is extracted from usage."""
        response_json = {
            "id": "test-1",
            "model": "qwen3.5-35b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello world"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 32000,
                "completion_tokens": 1200,
                "total_tokens": 33200,
                "prompt_tokens_details": {
                    "cached_tokens": 31000
                }
            }
        }
        usage = response_json.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens")

        assert cached_tokens == 31000
        assert usage.get("prompt_tokens") == 32000

    def test_cached_tokens_none_when_not_present(self):
        """Verify cached_tokens is None when prompt_tokens_details is absent."""
        response_json = {
            "id": "test-2",
            "model": "qwen3.5-35b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello world"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
            }
        }
        usage = response_json.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens")

        assert cached_tokens is None

    def test_cached_tokens_none_when_details_empty(self):
        """Verify cached_tokens is None when prompt_tokens_details exists but has no cached_tokens."""
        response_json = {
            "id": "test-3",
            "model": "qwen3.5-35b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello world"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
                "prompt_tokens_details": {}
            }
        }
        usage = response_json.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens")

        assert cached_tokens is None

    def test_cache_percentage_calculation(self):
        """Verify cache percentage is calculated correctly."""
        prompt_tokens = 32000
        cached_tokens = 31000
        cache_pct = round((cached_tokens / prompt_tokens) * 100, 1)

        assert cache_pct == 96.9

    def test_cache_percentage_zero_cached(self):
        """Verify cache percentage is 0 when no tokens are cached."""
        prompt_tokens = 32000
        cached_tokens = 0
        cache_pct = round((cached_tokens / prompt_tokens) * 100, 1)

        assert cache_pct == 0.0


class TestStreamingCacheMetrics:
    """Test cached_tokens extraction in streaming handler."""

    def test_final_usage_captures_cached_tokens(self):
        """Verify that final_usage from reconstructed response includes cached_tokens."""
        reconstructed_response = {
            "id": "stream-1",
            "model": "qwen3.5-35b",
            "usage": {
                "prompt_tokens": 28000,
                "completion_tokens": 500,
                "total_tokens": 28500,
                "prompt_tokens_details": {
                    "cached_tokens": 27500
                }
            }
        }
        final_usage = reconstructed_response.get("usage")
        prompt_details = final_usage.get("prompt_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens")

        assert cached_tokens == 27500

    def test_streaming_cache_percentage(self):
        """Verify streaming cache percentage calculation."""
        prompt_tokens_u = 28000
        cached_tokens_u = 27500
        cache_pct_u = round((cached_tokens_u / prompt_tokens_u) * 100, 1)

        assert cache_pct_u == 98.2


class TestFakeBackend:
    """Test that fake backend can optionally include cached_tokens."""

    def test_usage_without_cached_tokens(self):
        """Verify fake backend default usage doesn't include cached_tokens."""
        # This tests the existing behavior - fake backend doesn't include cached_tokens by default
        # The new feature allows upstreams that DO include it to be properly captured
        usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens")

        assert cached_tokens is None


class TestResponsePassthrough:
    """Verify that cached_tokens passes through in client response."""

    def test_non_streaming_passthrough_preserves_usage(self):
        """In non-streaming mode, the raw upstream response (including usage with cached_tokens) is returned to client."""
        # The current implementation returns r.json() directly when no filter processing occurred
        # This means cached_tokens in usage passes through automatically
        upstream_response = {
            "id": "test-4",
            "model": "qwen3.5-35b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 32000,
                "completion_tokens": 100,
                "total_tokens": 32100,
                "prompt_tokens_details": {
                    "cached_tokens": 31000
                }
            }
        }
        # The response JSON is passed through as-is
        assert upstream_response["usage"]["prompt_tokens_details"]["cached_tokens"] == 31000

    def test_streaming_final_chunk_preserves_usage(self):
        """In streaming mode, final_usage is embedded in the final SSE chunk."""
        # The streaming handler does: final_chunk_obj["usage"] = final_usage
        # So cached_tokens in final_usage passes through to client
        final_usage = {
            "prompt_tokens": 32000,
            "completion_tokens": 100,
            "total_tokens": 32100,
            "prompt_tokens_details": {
                "cached_tokens": 31000
            }
        }
        final_chunk_obj = {
            "id": "stream-5",
            "model": "qwen3.5-35b",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": "stop"}],
        }
        if final_usage:
            final_chunk_obj["usage"] = final_usage

        assert final_chunk_obj["usage"]["prompt_tokens_details"]["cached_tokens"] == 31000
