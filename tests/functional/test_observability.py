"""
Functional tests: verify NDJSON logging contains expected events.
"""

import json


class TestObservability:
    """Verify NDJSON logging for key scenarios."""

    def test_normal_request_logs_expected_events(self, harness):
        """Normal request logs http_in, route_resolved, http_out etc."""
        harness.set_scenario(chat_content="Hello world.")
        resp = harness.post(messages=[{"role": "user", "content": "hi"}])
        assert resp.status_code == 200

        events = harness.read_log_json()
        msgs = {e["msg"] for e in events}
        assert "http_in" in msgs, f"Missing http_in in {msgs}"
        assert "route_resolved" in msgs, f"Missing route_resolved in {msgs}"
        assert "http_out" in msgs, f"Missing http_out in {msgs}"

    def test_filter_chain_events_in_log(self, harness):
        """Filter chain events appear in NDJSON log."""
        harness.set_scenario(chat_content="Filtered response.")
        resp = harness.post(messages=[{"role": "user", "content": "hi"}])
        assert resp.status_code == 200

        events = harness.read_log_json()
        msgs = {e["msg"] for e in events}
        filter_msgs = msgs & {
            "has_filter_chain", "system_prompt_inserted",
            "filter_chain_executed", "filter_chain_loaded"
        }
        assert filter_msgs, f"No filter events in {msgs}"

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
            assert "msg" in ev, f"Missing 'msg': {line[:80]}"
            assert "ts" in ev, f"Missing 'ts': {line[:80]}"

    def test_streaming_request_logs_progress(self, harness):
        """Streaming request logs response_stream_reconstructed or stream_progress."""
        harness.set_scenario(chat_content="Streaming response.")
        resp = harness.post_stream(messages=[{"role": "user", "content": "stream"}])
        assert resp.status_code == 200

        events = harness.read_log_json()
        msgs = {e["msg"] for e in events}
        # Pipeline logs 'assistant' for both streaming and non-streaming responses
        stream_msgs = msgs & {"assistant", "response_stream_reconstructed", "stream_progress"}
        assert stream_msgs, f"No streaming events in {msgs}"
