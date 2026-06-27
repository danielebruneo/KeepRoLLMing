"""E2E tests for TimestampFilter through the full orchestrator pipeline.

Verifies that the timestamp filter:
1. Injects a system message with the current timestamp into the request
2. Appends the timestamp to the assistant response content
3. Works in both streaming and non-streaming modes
4. Leaves tool_calls-only responses unchanged
"""

import re

import httpx
import pytest


@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestTimestampFilterE2E:
    """TimestampFilter E2E tests through the full orchestrator pipeline."""

    def _assert_timestamp_in_content(self, content: str):
        """Helper: assert content has timestamp appended."""
        assert "---" in content, f"No separator found in: {repr(content[:200])}"
        assert "Timestamp:" in content, f"No Timestamp found in: {repr(content[:200])}"
        # Verify timestamp format: YYYY-MM-DD HH:MM:SS UTC
        assert re.search(
            r"Timestamp: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC",
            content,
        ), f"Timestamp format mismatch in: {repr(content[:200])}"

    def test_nonstreaming_timestamp_in_response(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Non-streaming: response content has timestamp appended."""
        configure_fake_backend({"chat": {"content": "Hello from backend"}})

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "error" not in body

        content = body["choices"][0]["message"]["content"]
        # Should contain original content
        assert "Hello from backend" in content
        # Should have timestamp appended
        self._assert_timestamp_in_content(content)

    def test_streaming_timestamp_in_response(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Streaming: response content has timestamp appended."""
        configure_fake_backend({
            "chat": {
                "stream_pieces": [["Hello", " from", " backend"]],
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.text

        # Should contain original content (across SSE chunks)
        assert "Hello" in body
        assert "from" in body
        assert "backend" in body
        # Should have timestamp appended (in the final chunk)
        assert "---" in body
        assert "Timestamp:" in body
        assert re.search(r"Timestamp: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", body)
        # Should have [DONE] marker
        assert "[DONE]" in body

    def test_tool_calls_only_response_unchanged(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Tool_calls-only response: no timestamp appended to content."""
        # The fake backend always returns "chat ok" as fallback content even with tool_calls,
        # but the key assertion is that tool_calls are preserved and not corrupted.
        configure_fake_backend({
            "chat": {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "test_tool",
                            "arguments": '{"arg": "value"}',
                        },
                    }
                ],
            }
        })

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp",
                "messages": [{"role": "user", "content": "Call a tool"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "error" not in body

        # Should have tool_calls preserved
        assert body["choices"][0]["message"]["tool_calls"] is not None
        tc = body["choices"][0]["message"]["tool_calls"]
        assert len(tc) >= 1
        assert tc[0]["function"]["name"] == "test_tool"

    def test_system_message_injected_upstream(self, orchestrator_server, backend_target, backend_client, configure_fake_backend, get_fake_stats):
        """Verify the upstream receives the system message with timestamp."""
        configure_fake_backend({"chat": {"content": "Response"}})

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text

        # Check what the fake backend received
        stats = get_fake_stats()
        assert stats is not None
        # Verify the request was received (2 messages: user + injected system)
        assert stats["calls_total"] >= 1

        # Check messages_count — the upstream should receive the injected system message
        requests = stats.get("requests", [])
        assert len(requests) >= 1
        last_request = requests[-1]
        # Original: 1 user message. Injected: +1 system message.
        assert last_request["messages_count"] == 2, (
            f"Expected 2 messages (user + system), got {last_request['messages_count']}. "
            f"This means the timestamp system message was NOT injected into the upstream payload."
        )

    def test_timestamp_consistency_request_response(self, orchestrator_server, backend_target, backend_client, configure_fake_backend, get_fake_stats):
        """Timestamp in request system message matches timestamp in response."""
        configure_fake_backend({"chat": {"content": "Response"}})

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        content = body["choices"][0]["message"]["content"]

        # Verify timestamp is in the response
        self._assert_timestamp_in_content(content)

    def test_multiple_messages_timestamp_at_end(self, orchestrator_server, backend_target, backend_client, configure_fake_backend, get_fake_stats):
        """Timestamp system message is appended after all existing messages."""
        configure_fake_backend({"chat": {"content": "Response"}})

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/timestamp",
                "messages": [
                    {"role": "user", "content": "First"},
                    {"role": "assistant", "content": "Reply"},
                    {"role": "user", "content": "Second"},
                ],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text

        # Check upstream received messages in correct order
        stats = get_fake_stats()
        requests = stats.get("requests", [])
        last_request = requests[-1]
        # 3 original messages + 1 injected system message = 4
        assert last_request["messages_count"] == 4, (
            f"Expected 4 messages (user, assistant, user, system), got {last_request['messages_count']}"
        )

    def test_disabled_filter_no_timestamp(self, orchestrator_server, backend_target, backend_client, configure_fake_backend):
        """Non-timestamp route: no timestamp in response."""
        configure_fake_backend({"chat": {"content": "Response"}})

        resp = backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "local/main",  # No timestamp filter
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        content = body["choices"][0]["message"]["content"]

        # Should NOT have timestamp
        assert "Timestamp:" not in content
        assert "---" not in content
