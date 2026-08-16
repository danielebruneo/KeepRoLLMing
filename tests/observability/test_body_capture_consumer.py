"""Unit tests for BodyCaptureConsumer (Phase O11).

Tests:
- errors_only policy captures on upstream_error events
- errors_only policy captures on request.lifecycle.failed events
- errors_only policy captures on streaming.handler_error events
- disabled policy captures nothing
- max body size truncation with metadata when body exceeds 50MB
- directory/file creation matches expected structure
- consumer failure isolation — persistence error does not raise from event handler
- NoOpRedactor passes data through unchanged
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from keeprollming.observability.body_capture_consumer import BodyCaptureConsumer
from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.redactor import NoOpRedactor, Redactor


class TestBodyCaptureConsumerErrorsOnly:
    """Test errors_only capture policy."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    @pytest.fixture
    def consumer(self, tmp_dir):
        return BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")

    def test_captures_on_upstream_error_event(self, consumer, tmp_dir):
        """errors_only policy captures on execution.chat.upstream_error events."""
        event = RuntimeEvent(
            type="execution.chat.upstream_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={
                "status": 500,
                "url": "http://upstream/v1/chat/completions",
                "route": "default",
                "upstream_model": "gpt-4",
                "body": '{"error": {"message": "internal error"}}',
            },
            req_id="test-req-001",
            level="ERROR",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-001"
        assert capture_dir.exists()
        assert (capture_dir / "meta.json").exists()
        assert (capture_dir / "upstream.error.json").exists()

        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["req_id"] == "test-req-001"
        assert meta["policy"] == "errors_only"
        assert meta["trigger_event"] == "execution.chat.upstream_error"
        assert meta["status"] == 500

    def test_captures_on_request_lifecycle_failed(self, consumer, tmp_dir):
        """errors_only policy captures on request.lifecycle.failed events."""
        event = RuntimeEvent(
            type="request.lifecycle.failed",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="lifecycle"),
            data={
                "error": "connection timeout",
                "status": 504,
            },
            req_id="test-req-002",
            level="ERROR",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-002"
        assert capture_dir.exists()
        assert (capture_dir / "meta.json").exists()

        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["trigger_event"] == "request.lifecycle.failed"
        assert meta["status"] == 504

    def test_captures_on_streaming_handler_error(self, consumer, tmp_dir):
        """errors_only policy captures on execution.streaming.handler_error events."""
        event = RuntimeEvent(
            type="execution.streaming.handler_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="streaming", component="handler"),
            data={
                "error": "pipeline processing failed",
                "route": "streaming-route",
                "upstream_model": "claude-3",
            },
            req_id="test-req-003",
            level="ERROR",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-003"
        assert capture_dir.exists()
        assert (capture_dir / "meta.json").exists()

        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["trigger_event"] == "execution.streaming.handler_error"
        assert meta["route"] == "streaming-route"

    def test_no_capture_on_non_error_event(self, consumer, tmp_dir):
        """errors_only policy does not capture non-error events."""
        event = RuntimeEvent(
            type="request.lifecycle.completed",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="lifecycle"),
            data={"status": 200},
            req_id="test-req-ok",
            level="INFO",
        )

        consumer(event)

        # No capture directory should be created
        assert not (tmp_dir / "test-req-ok").exists()


class TestBodyCaptureConsumerDisabled:
    """Test disabled policy."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    @pytest.fixture
    def consumer(self, tmp_dir):
        return BodyCaptureConsumer(base_dir=tmp_dir, policy="disabled")

    def test_disabled_policy_captures_nothing(self, consumer, tmp_dir):
        """disabled policy captures nothing."""
        event = RuntimeEvent(
            type="execution.chat.upstream_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={"status": 500, "body": "error"},
            req_id="test-req-disabled",
            level="ERROR",
        )

        consumer(event)

        assert not any(tmp_dir.iterdir())


class TestBodyCaptureConsumerTruncation:
    """Test max body size truncation."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_truncation_with_metadata_when_body_exceeds_50mb(self, tmp_dir):
        """max body size truncation with metadata when body exceeds 50MB."""
        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")

        # Create a large body that exceeds 50MB when serialized
        large_body = {"messages": [{"content": "x" * (60 * 1024 * 1024)}]}

        event = RuntimeEvent(
            type="execution.chat.upstream_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={"status": 500, "body": large_body},
            req_id="test-req-large",
            level="ERROR",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-large"
        error_file = capture_dir / "upstream.error.json"

        assert error_file.exists()
        content = json.loads(error_file.read_text())
        assert content.get("_truncated") is True
        assert "_original_size_bytes" in content


class TestBodyCaptureConsumerDirectoryStructure:
    """Test directory/file creation."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_directory_file_creation_matches_expected_structure(self, tmp_dir):
        """directory/file creation matches expected structure."""
        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")

        event = RuntimeEvent(
            type="execution.chat.upstream_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={
                "status": 400,
                "route": "test-route",
                "upstream_model": "gpt-3.5-turbo",
                "body": '{"error": "bad request"}',
                "request_payload": {"model": "gpt-3.5-turbo", "messages": []},
            },
            req_id="test-req-structure",
            level="ERROR",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-structure"
        assert capture_dir.exists()
        assert (capture_dir / "meta.json").exists()
        assert (capture_dir / "upstream.error.json").exists()
        assert (capture_dir / "request.raw_in.json").exists()

        # Verify content
        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["route"] == "test-route"
        assert meta["upstream_model"] == "gpt-3.5-turbo"


class TestBodyCaptureConsumerFailureIsolation:
    """Test consumer failure isolation."""

    def test_persistence_error_does_not_raise_from_event_handler(self, tmp_path):
        """consumer failure isolation — persistence error does not raise from event handler."""
        # Use a directory that can't be written to
        unwritable_dir = tmp_path / "unwritable"
        unwritable_dir.mkdir()
        os.chmod(unwritable_dir, 0o000)

        try:
            consumer = BodyCaptureConsumer(base_dir=unwritable_dir, policy="errors_only")

            event = RuntimeEvent(
                type="execution.chat.upstream_error",
                timestamp_ns=1_000_000_000_000_000_000,
                source=EventSource(domain="execution", component="chat"),
                data={"status": 500, "body": "error"},
                req_id="test-req-fail",
                level="ERROR",
            )

            # Should not raise
            consumer(event)
        finally:
            os.chmod(unwritable_dir, 0o755)


class TestNoOpRedactor:
    """Test NoOpRedactor."""

    def test_passes_data_through_unchanged(self):
        """NoOpRedactor passes data through unchanged."""
        redactor = NoOpRedactor()

        # Test with dict
        data = {"key": "value", "nested": {"a": 1}}
        assert redactor.redact(data) == data

        # Test with list
        data = [1, 2, 3]
        assert redactor.redact(data) == data

        # Test with string
        data = "hello world"
        assert redactor.redact(data) == data


class TestBodyCaptureConsumerMissingReqId:
    """Test behavior when req_id is missing."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_no_capture_when_req_id_missing(self, tmp_dir):
        """No capture directory created when req_id is None."""
        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")

        event = RuntimeEvent(
            type="execution.chat.upstream_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={"status": 500, "body": "error"},
            req_id=None,
            level="ERROR",
        )

        consumer(event)

        assert not any(tmp_dir.iterdir())


class TestBodyCaptureConsumerStreamingNoBody:
    """Test streaming error capture when no body is available."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_captures_meta_only_when_no_error_body(self, tmp_dir):
        """Streaming errors with only metadata capture meta.json without upstream.error.json."""
        consumer = BodyCaptureConsumer(base_dir=tmp_dir, policy="errors_only")

        event = RuntimeEvent(
            type="execution.streaming.handler_error",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="streaming", component="handler"),
            data={
                "error": "connection reset",
                "route": "streaming-route",
                "upstream_model": "claude-3",
            },
            req_id="test-stream-no-body",
            level="ERROR",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-stream-no-body"
        assert capture_dir.exists()
        assert (capture_dir / "meta.json").exists()
        # No upstream.error.json when body is not available
        assert not (capture_dir / "upstream.error.json").exists()
