"""E2E test for the streaming pipeline with the nudge filter.

This test verifies that the streaming pipeline correctly handles lazy responses
and triggers continuation through the only streaming path.
"""

from __future__ import annotations

import asyncio

import pytest

from keeprollming.orchestrator.pipeline import Pipeline


class TestNudgeStreamingPipeline:
    """Verify the streaming pipeline with the nudge filter."""

    def test_pipeline_with_nudge_continues_lazy_response(self):
        """The nudge finalizer continues a lazy response in the stream."""
        pipeline = Pipeline.from_route_config(
            {
                "model_nudge": {
                    "enabled": True,
                    "trigger_patterns": [":$"],
                    "nudge_message": "Continue.",
                    "max_attempts": 3,
                },
            },
        )
        assert pipeline is not None

        first_attempt = True

        async def mock_upstream(payload):
            nonlocal first_attempt
            # First attempt: lazy response
            if first_attempt:
                first_attempt = False
                yield b"data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Here is the list:\"}}]}\n\n"
                yield b"data: [DONE]\n\n"
            else:
                # Continuation
                yield b"data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\" item 1, item 2.\"}}]}\n\n"
                yield b"data: [DONE]\n\n"

        async def collect_chunks():
            chunks = []
            async for chunk in pipeline.run_stream(
                {
                    "messages": [{"role": "user", "content": "List items"}],
                    "stream": True,
                },
                "test-req-v2-nudge",
                "test-model",
                "test-route",
                "http://test",
                upstream_stream=mock_upstream,
            ):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(collect_chunks())

        assert len(chunks) > 0, "No chunks received"

        # The response should contain both the lazy prefix and the continuation
        full_response = b"".join(chunks).decode("utf-8", errors="ignore")
        assert "Here is the list:" in full_response
        assert "item 1, item 2" in full_response
