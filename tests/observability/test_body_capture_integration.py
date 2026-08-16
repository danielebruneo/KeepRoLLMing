"""Integration tests for BodyCaptureConsumer (Phase O11).

Tests:
- Non-streaming upstream error → capture created with correct boundaries and content
- Streaming failure → capture created (new coverage vs current dump)
- Multiple error events for same req_id → captures grouped in same directory
- Body Capture output parity with dump_failed_payload()
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from keeprollming.observability.body_capture_consumer import BodyCaptureConsumer
from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events import EventSource, RuntimeEvent


class TestNonStreamingUpstreamErrorCapture:
    """Test non-streaming upstream error capture end-to-end."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_non_streaming_upstream_error_creates_capture(self, tmp_dir):
        """Non-streaming upstream error → capture created with correct boundaries and content."""
        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")
        dispatcher = EventDispatcher()
        dispatcher.subscribe("execution.chat", consumer)

        # Simulate the event emitted by chat_completions.py on upstream error
        event = RuntimeEvent(
            type="execution.chat.upstream_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={
                "status": 429,
                "url": "https://api.openai.com/v1/chat/completions",
                "route": "openai-default",
                "upstream_model": "gpt-4-turbo",
                "body": '{"error":{"type":"rate_limit","message":"Rate limit exceeded"}}',
            },
            req_id="int-test-non-streaming-001",
            level="ERROR",
        )

        dispatcher.emit(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "int-test-non-streaming-001"
        assert capture_dir.exists()

        # Verify meta.json
        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["req_id"] == "int-test-non-streaming-001"
        assert meta["policy"] == "errors_only"
        assert meta["trigger_event"] == "execution.chat.upstream_error"
        assert meta["status"] == 429
        assert meta["route"] == "openai-default"
        assert meta["upstream_model"] == "gpt-4-turbo"

        # Verify upstream.error.json
        error_body = json.loads((capture_dir / "upstream.error.json").read_text())
        assert error_body["error"]["type"] == "rate_limit"


class TestStreamingFailureCapture:
    """Test streaming failure capture (new coverage vs current dump)."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_streaming_failure_creates_capture(self, tmp_dir):
        """Streaming failure → capture created (new coverage vs current dump)."""
        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")
        dispatcher = EventDispatcher()
        dispatcher.subscribe("execution.streaming", consumer)

        # Simulate streaming handler error event
        event = RuntimeEvent(
            type="execution.streaming.handler_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="streaming", component="handler"),
            data={
                "error": "ConnectionResetError: Connection reset by peer",
                "route": "claude-streaming",
                "upstream_url": "https://api.anthropic.com/v1/messages",
                "upstream_model": "claude-3-opus",
            },
            req_id="int-test-streaming-001",
            level="ERROR",
        )

        dispatcher.emit(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "int-test-streaming-001"
        assert capture_dir.exists()

        # Verify meta.json captures streaming context
        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["trigger_event"] == "execution.streaming.handler_error"
        assert meta["route"] == "claude-streaming"
        assert meta["upstream_model"] == "claude-3-opus"


class TestMultipleErrorEventsSameReqId:
    """Test multiple error events for same req_id."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_multiple_error_events_grouped_in_same_directory(self, tmp_dir):
        """Multiple error events for same req_id → captures grouped in same directory."""
        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")
        dispatcher = EventDispatcher()
        dispatcher.subscribe("execution.chat", consumer)
        dispatcher.subscribe("request.lifecycle", consumer)

        req_id = "int-test-multi-001"

        # First error: upstream_error
        event1 = RuntimeEvent(
            type="execution.chat.upstream_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={
                "status": 500,
                "route": "default",
                "body": '{"error": "internal"}',
            },
            req_id=req_id,
            level="ERROR",
        )

        # Second error: request.lifecycle.failed
        event2 = RuntimeEvent(
            type="request.lifecycle.failed",
            timestamp_ns=1_000_000_000_000_000_001,
            source=EventSource(domain="request", component="lifecycle"),
            data={
                "error": "upstream failed",
                "status": 500,
            },
            req_id=req_id,
            level="ERROR",
        )

        dispatcher.emit(event1)
        dispatcher.emit(event2)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / req_id
        assert capture_dir.exists()

        # Both events contribute to the same directory
        meta_files = list(capture_dir.glob("meta.json"))
        assert len(meta_files) == 1  # meta.json is overwritten by second event


class TestBodyCaptureDumpParity:
    """Test Body Capture output parity with dump_failed_payload()."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_parity_with_dump_failed_payload(self, tmp_dir):
        """Body Capture output for errors_only contains same data as current dump_failed_payload() would produce.

        Uses real event emission path via emit_upstream_error() to verify that
        request_payload is actually carried in the emitted event (CORRECTION-001).
        """
        from keeprollming.observability import events_execution as _exec

        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")
        dispatcher = EventDispatcher()
        dispatcher.subscribe("execution.chat", consumer)

        # Same data that chat_completions.py passes to both emit_upstream_error()
        # and dump_failed_payload() — verifying parity between the two outputs.
        req_id = "int-test-parity-001"
        resp_status = 400
        resp_body = '{"error":{"type":"invalid_request","message":"model not found"}}'
        request_payload = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        }
        upstream_model = "gpt-4"
        upstream_url = "https://api.openai.com/v1/chat/completions"
        route = "openai-default"

        # Use real emission path — this is what chat_completions.py actually calls
        _exec.emit_upstream_error(
            req_id=req_id,
            status=resp_status,
            url=upstream_url,
            route=route,
            upstream_model=upstream_model,
            body=resp_body,
            request_payload=request_payload,
            dispatcher=dispatcher,
        )

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / req_id

        # Verify same req_id preserved
        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["req_id"] == req_id

        # Verify same error body preserved (full, not truncated)
        # Body is stored as raw string; compare parsed versions
        captured_error_raw = (capture_dir / "upstream.error.json").read_text()
        assert json.loads(captured_error_raw) == json.loads(resp_body)

        # Verify request payload preserved — this is the CORRECTION-001 assertion:
        # emit_upstream_error() now carries request_payload, so BodyCaptureConsumer
        # can capture it, establishing parity with dump_failed_payload().
        captured_payload = json.loads((capture_dir / "request.raw_in.json").read_text())
        assert captured_payload == request_payload
