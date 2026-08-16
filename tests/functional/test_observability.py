"""
Functional tests: verify structured JSON projection contracts.
"""

import json

import pytest


class TestObservability:
    """Verify NDJSON logging for key scenarios."""

    def test_normal_request_logs_expected_events(self, harness):
        """Normal request logs http_in, route_resolved, http_out etc."""
        harness.set_scenario(chat_content="Hello world.")
        resp = harness.post(messages=[{"role": "user", "content": "hi"}])
        assert resp.status_code == 200

        events = harness.read_log_json()
        event_types = {event["type"] for event in events}
        assert "execution.chat.http_in" in event_types, event_types
        assert "execution.chat.route_resolved" in event_types, event_types
        assert "execution.chat.http_out" in event_types, event_types

    def test_filter_chain_events_in_log(self, harness):
        """Filter chain events appear in NDJSON log."""
        harness.set_scenario(chat_content="Filtered response.")
        resp = harness.post(messages=[{"role": "user", "content": "hi"}])
        assert resp.status_code == 200

        events = harness.read_log_json()
        routed_filter_sets = [
            event["data"].get("filters", [])
            for event in events
            if event["type"] == "execution.chat.request_route"
        ]
        assert ["system_prompt", "model_nudge"] in routed_filter_sets, (
            f"No route event with the configured filters: {routed_filter_sets}"
        )

    def test_json_log_is_valid_ndjson(self, harness):
        """Every line in the NDJSON log is valid JSON with required fields."""
        harness.set_scenario(chat_content="Valid JSON test.")
        harness.post(messages=[{"role": "user", "content": "test"}])

        log_path = harness.log_path
        if not log_path.exists():
            pytest.skip("No log file")

        for line in log_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            assert "type" in ev, f"Missing 'type': {line[:80]}"
            assert "timestamp_ms" in ev, f"Missing 'timestamp_ms': {line[:80]}"
            assert isinstance(ev.get("data"), dict), f"Missing event data: {line[:80]}"

    def test_streaming_request_logs_progress(self, harness):
        """Streaming request logs response_stream_reconstructed or stream_progress."""
        harness.set_scenario(chat_content="Streaming response.")
        resp = harness.post_stream(messages=[{"role": "user", "content": "stream"}])
        assert resp.status_code == 200

        events = harness.read_log_json()
        event_types = {event["type"] for event in events}
        stream_events = {
            "execution.streaming.progress",
            "execution.chat.assistant",
            "execution.streaming.complete",
        }
        assert event_types & stream_events, f"No streaming events in {event_types}"
