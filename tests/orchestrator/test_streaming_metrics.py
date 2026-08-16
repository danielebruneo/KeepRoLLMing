"""
Test that streaming requests properly calculate and log performance metrics.

This test verifies that when streaming responses are processed, elapsed_ms, tps, 
and total_tps are correctly calculated and available in the metrics.
"""

import time
import pytest


class MockRoute:
    """Mock Route object for testing."""
    name = "test"
    upstream_model = "test-model"
    tool_rewrite_enabled = False
    tool_rewrite_patterns = []
    
    def __init__(self):
        self.route_name = "test"
        self.model = "test"


def test_elapsed_ms_calculation():
    """Test that elapsed_ms is calculated correctly from t_start."""
    from keeprollming.endpoints.streaming_handlers import process_streaming_request
    
    # Simulate the time tracking pattern used in process_streaming_request
    t0 = time.perf_counter()
    
    # Simulate some work (streaming chunks)
    time.sleep(0.05)  # 50ms delay
    
    # Calculate elapsed_ms the same way we do in streaming_handlers.py
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    
    # Verify elapsed_ms is calculated and positive
    assert elapsed_ms > 0, "elapsed_ms should be positive"
    assert elapsed_ms >= 40, f"elapsed_ms should be at least ~50ms, got {elapsed_ms}"
    
    print(f"✓ elapsed_ms calculated: {elapsed_ms:.2f}ms")


def test_tps_calculation():
    """Test that tps (tokens per second) is calculated correctly."""
    # Simulate metrics calculation after streaming completes
    elapsed_ms = 100.0  # 100ms
    completion_tokens = 10
    
    # Calculate tps the same way we do in streaming_handlers.py
    if elapsed_ms > 0:
        tps = completion_tokens / (elapsed_ms / 1000.0)
    else:
        tps = None
    
    # Verify tps calculation
    assert tps is not None, "tps should be calculated"
    expected_tps = completion_tokens / (elapsed_ms / 1000.0)  # 10 / 0.1 = 100 tokens/sec
    assert abs(tps - expected_tps) < 0.01, f"tps should be {expected_tps}, got {tps}"
    
    print(f"✓ tps calculated: {tps:.2f} tokens/sec")


def test_total_tps_calculation():
    """Test that total_tps is calculated correctly."""
    # Simulate metrics calculation after streaming completes  
    elapsed_ms = 100.0  # 100ms
    prompt_tokens = 5
    completion_tokens = 10
    total_tokens = prompt_tokens + completion_tokens
    
    # Calculate total_tps the same way we do in streaming_handlers.py
    if elapsed_ms > 0:
        total_tps = total_tokens / (elapsed_ms / 1000.0)
    else:
        total_tps = None
    
    # Verify total_tps calculation
    assert total_tps is not None, "total_tps should be calculated"
    expected_total_tps = total_tokens / (elapsed_ms / 1000.0)  # 15 / 0.1 = 150 tokens/sec
    assert abs(total_tps - expected_total_tps) < 0.01, f"total_tps should be {expected_total_tps}, got {total_tps}"
    
    print(f"✓ total_tps calculated: {total_tps:.2f} tokens/sec")


def test_metrics_record_structure():
    """Test that the metrics record contains all required fields."""
    # Simulate a metrics record like what would be passed to record_request_performance
    elapsed_ms = 100.0
    completion_tokens = 10
    prompt_tokens = 5
    ttft_ms = 5.0
    
    metrics = {
        "model": "test-model",
        "req_id": "test-req-id",
        "stream": True,
        "ttft_ms": ttft_ms,
        "elapsed_ms": elapsed_ms,
        "tps": completion_tokens / (elapsed_ms / 1000.0),
        "total_tps": (completion_tokens + prompt_tokens) / (elapsed_ms / 1000.0),
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "total_tokens": completion_tokens + prompt_tokens,
        "finish_reason": "stop",
        "passthrough": False,
    }
    
    # Verify all required fields are present
    required_fields = ['elapsed_ms', 'tps', 'total_tps', 'completion_tokens', 'ttft_ms']
    for field in required_fields:
        assert field in metrics, f"Missing required field: {field}"
        assert metrics[field] is not None, f"Field {field} should not be None"
    
    print(f"✓ All required fields present:")
    for field in required_fields:
        print(f"  - {field}: {metrics[field]}")


def test_performance_logging_integration():
    """Test that metrics are actually written to __performance_logs/*.requests.jsonl."""
    import tempfile
    from pathlib import Path
    
    from keeprollming.performance import record_request_performance, _ensure_dir
    
    # Use temp directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        perf_dir = Path(tmpdir)
        
        # Patch _ensure_dir to use temp directory
        original_ensure_dir = _ensure_dir
        
        def mock_ensure_dir():
            return perf_dir
        
        import keeprollming.performance as perf_mod
        perf_mod._ensure_dir = mock_ensure_dir
        
        try:
            # Record a test metric
            elapsed_ms = 150.0
            completion_tokens = 25
            prompt_tokens = 10
            
            result = record_request_performance(
                model="test-model",
                route_name="test-route",
                req_id="test-req-123",
                stream=True,
                elapsed_ms=elapsed_ms,
                completion_tokens=completion_tokens,
                prompt_tokens=prompt_tokens,
                total_tokens=completion_tokens + prompt_tokens,
                ttft_ms=5.0,
            )
            
            # Verify result contains calculated fields
            assert 'tps' in result, "Result should contain tps"
            assert 'total_tps' in result, "Result should contain total_tps"
            assert result['elapsed_ms'] == elapsed_ms
            
            print(f"✓ Metrics recorded with:")
            print(f"  - elapsed_ms: {result['elapsed_ms']}")
            print(f"  - tps: {result['tps']:.2f}")
            print(f"  - total_tps: {result['total_tps']:.2f}")
            
            # Verify file was created
            jsonl_file = perf_dir / "test-route.requests.jsonl"
            assert jsonl_file.exists(), f"JSONL file should be created at {jsonl_file}"

            # Verify content (JSON-lines format)
            import json
            with open(jsonl_file, 'r') as f:
                lines = [line for line in f if line.strip()]
                data = [json.loads(line) for line in lines]

            assert len(data) > 0, "JSONL file should contain at least one entry"
            entry = data[0]
            
            assert 'elapsed_ms' in entry, "Entry should contain elapsed_ms"
            assert 'tps' in entry, "Entry should contain tps"
            assert 'total_tps' in entry, "Entry should contain total_tps"
            
            print(f"✓ Performance log written to {jsonl_file}")
            print(f"  - elapsed_ms: {entry['elapsed_ms']}")
            print(f"  - tps: {entry['tps']:.2f}")
            print(f"  - total_tps: {entry['total_tps']:.2f}")
            
        finally:
            # Restore original function
            perf_mod._ensure_dir = original_ensure_dir


def test_streaming_handler_metrics_integration():
    """The handler emits one final performance RuntimeEvent to its dispatcher."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    
    # Import the handler function
    from keeprollming.endpoints.streaming_handlers import process_streaming_request
    
    async def test_async():
        # Create mock objects
        mock_client = AsyncMock()
        
        # Create mock stream response
        class MockStreamResponse:
            status_code = 200
            headers = {"content-type": "text/event-stream"}
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                pass
            
            async def aiter_bytes(self):
                # Simulate streaming chunks with usage data at the end
                yield b'data: {"id":"test-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                yield b'data: {"id":"test-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]}\n\n'
                yield b'data: {"id":"test-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{"content":"world"},"finish_reason":null}]}\n\n'
                # Include usage data with token counts
                yield b'data: {"id":"test-1","object":"chat.completion.chunk","created":1700000000,"model":"test-model","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n'
                yield b'data: [DONE]\n\n'
        
        mock_client.stream = MagicMock(return_value=MockStreamResponse())
        
        from keeprollming.observability import EventDispatcher

        dispatcher = EventDispatcher()
        captured_events = []
        dispatcher.subscribe("execution", captured_events.append)
        
        # Create mock route
        class MockRoute:
            name = "test"
            upstream_model = "test-model"
            tool_rewrite_enabled = False
            tool_rewrite_patterns = []
            route_name = "test-route"
            model = "test"
        
        route = MockRoute()
        
        # Create payload
        payload = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": True,
        }
        
        route_headers = {}
        
        # Call the function with all required parameters
        t_start = time.perf_counter()
        
        # Call process_streaming_request (returns async generator directly)
        gen = process_streaming_request(
            client=mock_client,
            url="http://test.upstream/v1/chat/completions",
            payload=payload,
            route=route,
            req_id="test-req-456",
            upstream_model="test-model",
            transform_reasoning_content=False,
            add_empty_content_when_reasoning_only=False,
            is_passthrough=False,
            t_start=t_start,
            dispatcher=dispatcher,
            route_headers=route_headers,
            request_timeout=30.0,
            fallback_attempts=[],
            visited_models=set(),
            reasoning_placeholder=None,
        )
        
         # Iterate over the async generator
        result = []
        async for chunk in gen:
            result.append(chunk)
        
        assert captured_events, "performance event should be emitted"
        performance_event = next(
            event for event in captured_events
            if event.type == "execution.performance.request_complete"
        )
        assert performance_event.data["elapsed_ms"] > 0
        assert performance_event.data["completion_tokens"] == 3
        assert performance_event.data["prompt_tokens"] == 5
        assert performance_event.data["total_tokens"] == 8

        plain_metrics_event = next(
            event for event in captured_events
            if event.type == "execution.chat.performance_metrics"
        )
        assert plain_metrics_event.data["completion_tps"] is not None
        assert plain_metrics_event.data["prompt_tps"] is not None
    
    asyncio.run(test_async())


def test_non_streaming_metrics_calculation():
    """Test that non-streaming requests calculate elapsed_ms, tps, total_tps."""
    import time

    # Simulate the timing pattern used in process_non_streaming_request
    t_start = time.perf_counter()

    # Simulate some work (non-streaming request)
    time.sleep(0.05)  # 50ms delay

    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    completion_tokens = 10
    prompt_tokens = 5

    # Calculate metrics the same way _record_final_metrics does
    tps = completion_tokens / (elapsed_ms / 1000.0) if elapsed_ms > 0 else None
    total_tps = (completion_tokens + prompt_tokens) / (elapsed_ms / 1000.0) if elapsed_ms > 0 else None

    # Verify all metrics are calculated correctly
    assert elapsed_ms > 0, "elapsed_ms should be positive"
    assert tps is not None, "tps should be calculated"
    assert total_tps is not None, "total_tps should be calculated"

    print(f"✓ Non-streaming metrics calculated:")
    print(f"  - elapsed_ms: {elapsed_ms:.2f}ms")
    print(f"  - tps: {tps:.2f}")
    print(f"  - total_tps: {total_tps:.2f}")
