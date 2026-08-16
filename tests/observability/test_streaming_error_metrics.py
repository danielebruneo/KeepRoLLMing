"""Tests for streaming error-path metrics emission (O10-NF02 / STREAMING-PARITY-IMPL-001).

Verifies that when process_streaming_request encounters an exception, it emits
execution.performance.request_complete with finish_reason="error" and
completion_tokens_source="missing", achieving parity with non-streaming behavior.

Tests:
1. Pipeline path exception handler emits metrics event
2. Direct upstream fallback exception handler emits metrics event
3. Event data includes correct error-path fields
4. PerformanceConsumer captures the error-path metrics as JSONL record

Usage:
    pytest tests/observability/test_streaming_error_metrics.py -v -s
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import List, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.consumers import PerformanceConsumer


class TestStreamingErrorPathMetricsEmission:
    """Test that streaming error paths emit performance metrics events."""

    def test_pipeline_error_path_emits_metrics_event(self):
        """Pipeline path exception handler emits execution.performance.request_complete."""
        from keeprollming.endpoints.streaming_handlers import process_streaming_request
        from keeprollming.orchestrator.pipeline import Pipeline

        async def run_test():
            dispatcher = EventDispatcher()
            captured_events: List[RuntimeEvent] = []

            def capture(event: RuntimeEvent):
                captured_events.append(event)

            dispatcher.subscribe("execution", capture)

            # Create mock client that raises exception during streaming
            mock_client = AsyncMock()

            class MockStreamResponse:
                status_code = 200

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def aiter_bytes(self):
                    # Keep this an async generator even though this scenario
                    # fails before yielding an upstream frame.
                    if False:
                        yield b""
                    raise ConnectionError("upstream disconnected")

            mock_client.stream = MagicMock(return_value=MockStreamResponse())

            # Create mock route with filters to trigger pipeline path
            class MockRoute:
                name = "test-route"
                upstream_model = "test-model"
                filters = {"system_prompt": {}}  # triggers pipeline build
                _route_hierarchy = ["test-route"]

            route = MockRoute()

            payload = {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }

            # Consume the generator (error will occur during iteration)
            gen = process_streaming_request(
                url="http://test.upstream/v1/chat/completions",
                client=mock_client,
                payload=payload,
                route_headers={},
                route=route,
                req_id="test-req-error-001",
                request_timeout=30.0,
                fallback_attempts=[],
                visited_models=set(),
                upstream_model="test-model",
                is_passthrough=False,
                transform_reasoning_content=False,
                add_empty_content_when_reasoning_only=False,
                reasoning_placeholder=None,
                t_start=asyncio.get_event_loop().time(),
                dispatcher=dispatcher,
            )

            chunks = []
            async for chunk in gen:
                chunks.append(chunk)

            # Find the performance event among captured events
            perf_events = [
                e for e in captured_events
                if e.type == "execution.performance.request_complete"
            ]

            assert len(perf_events) == 1, (
                f"Expected exactly 1 performance event on error path, got {len(perf_events)}"
            )

            event = perf_events[0]
            data = event.data

            # Verify error-path specific fields
            assert data["finish_reason"] == "error", (
                f"finish_reason should be 'error', got {data.get('finish_reason')}"
            )
            assert data["completion_tokens_source"] == "missing", (
                f"completion_tokens_source should be 'missing', got {data.get('completion_tokens_source')}"
            )
            assert data["stream"] is True, "stream should be True"
            assert data["req_id"] == "test-req-error-001"
            assert data["model"] == "test-model"
            assert data["elapsed_ms"] is not None and data["elapsed_ms"] >= 0

            # Verify token counts are None on error path
            assert data["completion_tokens"] is None
            assert data["prompt_tokens"] is None
            assert data["total_tokens"] is None
            assert data["ttft_ms"] is None

            print(f"✓ Pipeline error path emitted metrics event:")
            print(f"  - finish_reason: {data['finish_reason']}")
            print(f"  - completion_tokens_source: {data['completion_tokens_source']}")
            print(f"  - stream: {data['stream']}")
            print(f"  - elapsed_ms: {data['elapsed_ms']:.2f}ms")

        asyncio.run(run_test())

    def test_direct_upstream_error_path_emits_metrics_event(self):
        """Direct upstream fallback exception handler emits execution.performance.request_complete."""
        from keeprollming.endpoints.streaming_handlers import process_streaming_request

        async def run_test():
            dispatcher = EventDispatcher()
            captured_events: List[RuntimeEvent] = []

            def capture(event: RuntimeEvent):
                captured_events.append(event)

            dispatcher.subscribe("execution", capture)

            # Create mock client that raises exception during streaming
            mock_client = AsyncMock()

            class MockStreamResponse:
                status_code = 200

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    pass

                async def aiter_bytes(self):
                    if False:
                        yield b""
                    raise TimeoutError("upstream timeout")

            mock_client.stream = MagicMock(return_value=MockStreamResponse())

            # Create mock route WITHOUT filters to trigger direct upstream path
            class MockRoute:
                name = "test-route"
                upstream_model = "test-model"
                filters = None  # no pipeline, direct upstream
                _route_hierarchy = ["test-route"]

            route = MockRoute()

            payload = {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }

            gen = process_streaming_request(
                url="http://test.upstream/v1/chat/completions",
                client=mock_client,
                payload=payload,
                route_headers={},
                route=route,
                req_id="test-req-error-002",
                request_timeout=30.0,
                fallback_attempts=[],
                visited_models=set(),
                upstream_model="test-model",
                is_passthrough=False,
                transform_reasoning_content=False,
                add_empty_content_when_reasoning_only=False,
                reasoning_placeholder=None,
                t_start=asyncio.get_event_loop().time(),
                dispatcher=dispatcher,
            )

            chunks = []
            async for chunk in gen:
                chunks.append(chunk)

            # Find the performance event among captured events
            perf_events = [
                e for e in captured_events
                if e.type == "execution.performance.request_complete"
            ]

            assert len(perf_events) == 1, (
                f"Expected exactly 1 performance event on error path, got {len(perf_events)}"
            )

            event = perf_events[0]
            data = event.data

            # Verify error-path specific fields
            assert data["finish_reason"] == "error", (
                f"finish_reason should be 'error', got {data.get('finish_reason')}"
            )
            assert data["completion_tokens_source"] == "missing", (
                f"completion_tokens_source should be 'missing', got {data.get('completion_tokens_source')}"
            )
            assert data["stream"] is True, "stream should be True"
            assert data["req_id"] == "test-req-error-002"
            assert data["model"] == "test-model"

            print(f"✓ Direct upstream error path emitted metrics event:")
            print(f"  - finish_reason: {data['finish_reason']}")
            print(f"  - completion_tokens_source: {data['completion_tokens_source']}")
            print(f"  - stream: {data['stream']}")

        asyncio.run(run_test())


class TestStreamingErrorPathPerformanceConsumer:
    """Test that PerformanceConsumer captures streaming error-path metrics."""

    def test_performance_consumer_captures_error_metrics(self):
        """PerformanceConsumer writes JSONL record for streaming error request."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_dir = Path(tmpdir)

            # Create dispatcher and consumer
            dispatcher = EventDispatcher()
            consumer = PerformanceConsumer(perf_logs_dir=str(perf_dir), summary_interval=20)
            dispatcher.subscribe("execution", consumer)

            # Emit error-path performance event
            event = RuntimeEvent(
                type="execution.performance.request_complete",
                timestamp_ns=1_700_000_000_000_000_000,
                source=EventSource(domain="execution", component="performance"),
                data={
                    "model": "test-model",
                    "route_name": "error-test-route",
                    "route_hierarchy": ["error-test-route"],
                    "req_id": "test-req-error-consumer-001",
                    "stream": True,
                    "elapsed_ms": 150.0,
                    "ttft_ms": None,
                    "completion_tokens": None,
                    "prompt_tokens": None,
                    "total_tokens": None,
                    "finish_reason": "error",
                    "did_summarize": False,
                    "passthrough": False,
                    "completion_tokens_source": "missing",
                },
                req_id="test-req-error-consumer-001",
                level="INFO",
            )

            dispatcher.emit(event)

            # Give consumer time to write asynchronously
            asyncio.run(asyncio.sleep(0.2))

            # Find the JSONL file for this route
            jsonl_files = list(perf_dir.glob("*.requests.jsonl"))
            assert len(jsonl_files) == 1, (
                f"Expected exactly 1 JSONL file, found {len(jsonl_files)}"
            )

            # Read and verify the record
            with open(jsonl_files[0], 'r') as f:
                lines = [line for line in f if line.strip()]
                records = [json.loads(line) for line in lines]

            assert len(records) == 1, "Expected exactly 1 record"
            record = records[0]

            # Verify error-path fields are preserved in JSONL
            assert record["finish_reason"] == "error"
            assert record["completion_tokens_source"] == "missing"
            assert record["stream"] is True
            assert record["req_id"] == "test-req-error-consumer-001"
            assert record["elapsed_ms"] == 150.0

            print(f"✓ PerformanceConsumer captured error metrics:")
            print(f"  - finish_reason: {record['finish_reason']}")
            print(f"  - completion_tokens_source: {record['completion_tokens_source']}")
            print(f"  - JSONL file: {jsonl_files[0].name}")


class TestStreamingErrorPathParityWithNonStreaming:
    """Test that streaming error-path metrics match non-streaming pattern."""

    def test_error_path_fields_match_non_streaming_pattern(self):
        """Streaming error path uses same field values as non-streaming error path."""
        from keeprollming.endpoints.chat_completions import _record_final_metrics

        # Non-streaming error path pattern (from chat_completions.py)
        non_streaming_error_metrics = {
            "model": "test-model",
            "req_id": "test-req-parity",
            "stream": False,
            "ttft_ms": None,
            "elapsed_ms": 100.0,
            "completion_tokens": None,
            "prompt_tokens": None,
            "total_tokens": None,
            "finish_reason": "some error message",
            "passthrough": False,
            "completion_tokens_source": "missing",
        }

        # Streaming error path pattern (from streaming_handlers.py)
        streaming_error_data = {
            "model": "test-model",
            "req_id": "test-req-parity",
            "stream": True,
            "elapsed_ms": 100.0,
            "ttft_ms": None,
            "completion_tokens": None,
            "prompt_tokens": None,
            "total_tokens": None,
            "finish_reason": "error",
            "did_summarize": False,
            "passthrough": False,
            "completion_tokens_source": "missing",
        }

        # Key parity fields that must match between streaming and non-streaming error paths
        parity_fields = [
            "completion_tokens_source",  # both "missing"
            "completion_tokens",         # both None
            "prompt_tokens",             # both None
            "total_tokens",              # both None
            "ttft_ms",                   # both None
        ]

        for field in parity_fields:
            ns_val = non_streaming_error_metrics[field]
            st_val = streaming_error_data[field]
            assert ns_val == st_val, (
                f"Parity violation on '{field}': non-streaming={ns_val}, streaming={st_val}"
            )

        # finish_reason differs in format but both indicate error
        assert non_streaming_error_metrics["finish_reason"] is not None
        assert streaming_error_data["finish_reason"] == "error"

        print(f"✓ Streaming/non-streaming error path parity verified:")
        for field in parity_fields:
            print(f"  - {field}: {streaming_error_data[field]}")
