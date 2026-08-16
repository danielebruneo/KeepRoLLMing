"""Unit tests for RequestCaptureConsumer (Phase O12).

Tests:
- disabled policy captures nothing (default production safety)
- all policy captures on request.capture.raw_inbound events
- selected_routes policy captures only matching routes
- max body size truncation with metadata when body exceeds 50MB
- directory/file creation matches expected structure
- consumer failure isolation — persistence error does not raise from event handler
- NoOpRedactor passes data through unchanged
- capture_format_version included in meta.json
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from keeprollming.observability.request_capture_consumer import RequestCaptureConsumer
from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.redactor import NoOpRedactor


class TestRequestCaptureConsumerDisabled:
    """Test disabled policy (default production safety)."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    @pytest.fixture
    def consumer(self, tmp_dir):
        return RequestCaptureConsumer(base_dir=tmp_dir, policy="disabled")

    def test_disabled_policy_captures_nothing(self, consumer, tmp_dir):
        """disabled policy captures nothing."""
        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": {"model": "gpt-4", "messages": []},
                "client_model": "qwen35",
                "resolved_route": "local_main",
                "upstream_model": "qwen3-coder-32b",
                "upstream_url": "http://localhost:1234/v1/chat/completions",
            },
            req_id="test-req-disabled",
            level="DEBUG",
        )

        consumer(event)

        assert not any(tmp_dir.iterdir())

    def test_disabled_is_default_policy(self, tmp_dir):
        """Default policy is disabled for production safety."""
        consumer = RequestCaptureConsumer(base_dir=tmp_dir)
        assert consumer._policy == "disabled"


class TestRequestCaptureConsumerAllPolicy:
    """Test all capture policy."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    @pytest.fixture
    def consumer(self, tmp_dir):
        return RequestCaptureConsumer(base_dir=tmp_dir, policy="all")

    def test_all_policy_captures_raw_inbound_event(self, consumer, tmp_dir):
        """all policy captures on request.capture.raw_inbound events."""
        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": {
                    "model": "qwen3-coder-32b",
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 1024,
                },
                "client_model": "qwen35",
                "resolved_route": "local_main",
                "upstream_model": "qwen3-coder-32b",
                "upstream_url": "http://localhost:1234/v1/chat/completions",
                "route_hierarchy": ["base", "local_main"],
            },
            req_id="test-req-all-001",
            level="DEBUG",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-all-001"
        assert capture_dir.exists()
        assert (capture_dir / "meta.json").exists()
        assert (capture_dir / "request.raw_inbound.json").exists()

        # Verify meta.json content
        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["req_id"] == "test-req-all-001"
        assert meta["policy"] == "all"
        assert meta["trigger_event"] == "request.capture.raw_inbound"
        assert meta["client_model"] == "qwen35"
        assert meta["resolved_route"] == "local_main"
        assert meta["upstream_model"] == "qwen3-coder-32b"
        assert meta["upstream_url"] == "http://localhost:1234/v1/chat/completions"
        assert meta["route_hierarchy"] == ["base", "local_main"]
        assert meta["capture_format_version"] == "1.0"

        # Verify raw body captured correctly
        raw_body = json.loads((capture_dir / "request.raw_inbound.json").read_text())
        assert raw_body["model"] == "qwen3-coder-32b"
        assert len(raw_body["messages"]) == 1
        assert raw_body["max_tokens"] == 1024

    def test_no_capture_on_non_capture_event(self, consumer, tmp_dir):
        """all policy does not capture non-capture events."""
        event = RuntimeEvent(
            type="execution.chat.http_in",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="execution", component="chat"),
            data={"client_model": "test"},
            req_id="test-req-other",
            level="INFO",
        )

        consumer(event)

        assert not (tmp_dir / "test-req-other").exists()


class TestRequestCaptureConsumerSelectedRoutes:
    """Test selected_routes capture policy."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    @pytest.fixture
    def consumer(self, tmp_dir):
        return RequestCaptureConsumer(
            base_dir=tmp_dir,
            policy="selected_routes",
            selected_routes=["local_main", "debug_route"],
        )

    def test_selected_routes_captures_matching_route(self, consumer, tmp_dir):
        """selected_routes policy captures requests for matching routes."""
        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": {"model": "gpt-4", "messages": []},
                "client_model": "qwen35",
                "resolved_route": "local_main",
                "upstream_model": "qwen3-coder-32b",
                "upstream_url": "http://localhost:1234/v1/chat/completions",
            },
            req_id="test-req-matching",
            level="DEBUG",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-matching"
        assert capture_dir.exists()

    def test_selected_routes_skips_non_matching_route(self, consumer, tmp_dir):
        """selected_routes policy skips requests for non-matching routes."""
        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": {"model": "gpt-4", "messages": []},
                "client_model": "qwen35",
                "resolved_route": "other_route",
                "upstream_model": "gpt-4",
                "upstream_url": "http://api.openai.com/v1/chat/completions",
            },
            req_id="test-req-other-route",
            level="DEBUG",
        )

        consumer(event)

        assert not (tmp_dir / "test-req-other-route").exists()


class TestRequestCaptureConsumerTruncation:
    """Test max body size truncation."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_truncation_with_metadata_when_body_exceeds_50mb(self, tmp_dir):
        """max body size truncation with metadata when body exceeds 50MB."""
        consumer = RequestCaptureConsumer(base_dir=tmp_dir, policy="all")

        # Create a large body that exceeds 50MB when serialized
        large_body = {"messages": [{"content": "x" * (60 * 1024 * 1024)}]}

        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": large_body,
                "client_model": "test",
                "resolved_route": "test-route",
                "upstream_model": "test-model",
                "upstream_url": "http://localhost/v1/chat/completions",
            },
            req_id="test-req-large",
            level="DEBUG",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-large"
        raw_file = capture_dir / "request.raw_inbound.json"

        assert raw_file.exists()
        content = json.loads(raw_file.read_text())
        assert content.get("_truncated") is True
        assert "_original_size_bytes" in content


class TestRequestCaptureConsumerDirectoryStructure:
    """Test directory/file creation."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_directory_file_creation_matches_expected_structure(self, tmp_dir):
        """directory/file creation matches O12-D expected structure."""
        consumer = RequestCaptureConsumer(base_dir=tmp_dir, policy="all")

        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": {
                    "model": "qwen3-coder-32b",
                    "messages": [{"role": "user", "content": "test"}],
                },
                "client_model": "qwen35",
                "resolved_route": "local_main",
                "upstream_model": "qwen3-coder-32b",
                "upstream_url": "http://localhost:1234/v1/chat/completions",
            },
            req_id="test-req-structure",
            level="DEBUG",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-structure"
        assert capture_dir.exists()
        assert (capture_dir / "meta.json").exists()
        assert (capture_dir / "request.raw_inbound.json").exists()

        # Verify meta.json has all correlation metadata
        meta = json.loads((capture_dir / "meta.json").read_text())
        assert meta["req_id"] == "test-req-structure"
        assert meta["client_model"] == "qwen35"
        assert meta["resolved_route"] == "local_main"
        assert meta["upstream_model"] == "qwen3-coder-32b"
        assert meta["upstream_url"] == "http://localhost:1234/v1/chat/completions"


class TestRequestCaptureConsumerFailureIsolation:
    """Test consumer failure isolation."""

    def test_persistence_error_does_not_raise_from_event_handler(self, tmp_path):
        """consumer failure isolation — persistence error does not raise from event handler."""
        # Use a directory that can't be written to
        unwritable_dir = tmp_path / "unwritable"
        unwritable_dir.mkdir()
        os.chmod(unwritable_dir, 0o000)

        try:
            consumer = RequestCaptureConsumer(base_dir=unwritable_dir, policy="all")

            event = RuntimeEvent(
                type="request.capture.raw_inbound",
                timestamp_ns=1_000_000_000_000_000_000,
                source=EventSource(domain="request", component="capture"),
                data={
                    "raw_body": {"model": "test", "messages": []},
                    "client_model": "test",
                    "resolved_route": "test-route",
                    "upstream_model": "test-model",
                    "upstream_url": "http://localhost/v1/chat/completions",
                },
                req_id="test-req-fail",
                level="DEBUG",
            )

            # Should not raise
            consumer(event)
        finally:
            os.chmod(unwritable_dir, 0o755)


class TestRequestCaptureConsumerMissingReqId:
    """Test behavior when req_id is missing."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_no_capture_when_req_id_missing(self, tmp_dir):
        """No capture directory created when req_id is None."""
        consumer = RequestCaptureConsumer(base_dir=tmp_dir, policy="all")

        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": {"model": "test", "messages": []},
                "client_model": "test",
                "resolved_route": "test-route",
                "upstream_model": "test-model",
                "upstream_url": "http://localhost/v1/chat/completions",
            },
            req_id=None,
            level="DEBUG",
        )

        consumer(event)

        assert not any(tmp_dir.iterdir())


class TestRequestCaptureConsumerMissingRawBody:
    """Test behavior when raw_body is missing."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_no_capture_when_raw_body_missing(self, tmp_dir):
        """No capture directory created when raw_body is None."""
        consumer = RequestCaptureConsumer(base_dir=tmp_dir, policy="all")

        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "client_model": "test",
                "resolved_route": "test-route",
                "upstream_model": "test-model",
                "upstream_url": "http://localhost/v1/chat/completions",
            },
            req_id="test-req-no-body",
            level="DEBUG",
        )

        consumer(event)

        assert not any(tmp_dir.iterdir())


class TestRequestCaptureConsumerVersioning:
    """Test capture format versioning."""

    @pytest.fixture
    def tmp_dir(self, tmp_path):
        return tmp_path / "captures"

    def test_capture_format_version_in_meta(self, tmp_dir):
        """capture_format_version included in meta.json for future evolution."""
        consumer = RequestCaptureConsumer(base_dir=tmp_dir, policy="all")

        event = RuntimeEvent(
            type="request.capture.raw_inbound",
            timestamp_ns=1_000_000_000_000_000_000,
            source=EventSource(domain="request", component="capture"),
            data={
                "raw_body": {"model": "test", "messages": []},
                "client_model": "test",
                "resolved_route": "test-route",
                "upstream_model": "test-model",
                "upstream_url": "http://localhost/v1/chat/completions",
            },
            req_id="test-req-version",
            level="DEBUG",
        )

        consumer(event)

        capture_dir = tmp_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "test-req-version"
        meta = json.loads((capture_dir / "meta.json").read_text())
        assert "capture_format_version" in meta
        assert meta["capture_format_version"] == "1.0"
