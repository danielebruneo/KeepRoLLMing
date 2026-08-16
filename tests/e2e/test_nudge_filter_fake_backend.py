"""Route-driven nudge E2E regressions using the shared server fixture."""

from __future__ import annotations

import pytest


@pytest.mark.e2e_fake
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestNudgeRetryE2E:
    """Nudge behaviour through the production HTTP route, without fixed ports."""

    @staticmethod
    def _request(backend_client, orchestrator_server, *, stream: bool):
        return backend_client.post(
            f"{orchestrator_server.base_url}/v1/chat/completions",
            json={
                "model": "internal/full",
                "messages": [{"role": "user", "content": "Continue"}],
                "stream": stream,
            },
        )

    def test_non_streaming_retries_up_to_configured_limit(
        self, orchestrator_server, backend_client, configure_fake_backend, get_fake_stats,
    ):
        configure_fake_backend({
            "chat": {
                "script": [
                    {"content": "Still thinking:"},
                    {"content": "Still thinking:"},
                    {"content": "Still thinking:"},
                    {"content": "Still thinking:"},
                ],
            },
        })

        response = self._request(backend_client, orchestrator_server, stream=False)

        assert response.status_code == 200, response.text
        assert get_fake_stats()["calls_by_kind"]["chat"] == 4
        assert response.json()["choices"][0]["message"]["content"] == (
            "Still thinking:\nStill thinking:\nStill thinking:\nStill thinking:"
        )

    def test_streaming_retries_and_keeps_one_terminal_sequence(
        self, orchestrator_server, backend_client, configure_fake_backend, get_fake_stats,
    ):
        configure_fake_backend({
            "chat": {
                "script": [
                    {"content": "I will say:", "stream_pieces": ["I will ", "say:"]},
                    {"content": "The complete continuation.", "stream_pieces": ["The complete continuation."]},
                ],
            },
        })

        response = self._request(backend_client, orchestrator_server, stream=True)

        assert response.status_code == 200, response.text
        assert get_fake_stats()["calls_by_kind"]["chat"] == 2
        # Rejected lazy attempts are not client-visible; the client receives
        # the accepted continuation and a single terminal sequence.
        assert "The complete continuation." in response.text
        assert response.text.count("data: [DONE]") == 1
        assert '"finish_reason": "stop"' in response.text

    @pytest.mark.parametrize("stream", [False, True])
    def test_complete_response_does_not_retry(
        self, orchestrator_server, backend_client, configure_fake_backend, get_fake_stats, stream,
    ):
        configure_fake_backend({
            "chat": {"content": "This response is complete.", "stream_pieces": [["This response is complete."]]},
        })

        response = self._request(backend_client, orchestrator_server, stream=stream)

        assert response.status_code == 200, response.text
        assert get_fake_stats()["calls_by_kind"]["chat"] == 1
