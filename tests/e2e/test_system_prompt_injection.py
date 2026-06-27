"""
Test that system prompt reaches upstream via process_non_streaming_request.
"""

import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSystemPromptInUpstreamPayload:
    """Direct handler test — no server needed."""

    @pytest.mark.asyncio
    async def test_system_prompt_injected(self):
        """System prompt is in the payload sent to upstream."""
        from keeprollming.endpoints.chat_completions import process_non_streaming_request

        payload = {
            "model": "local/sp_test",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }

        route = MagicMock()
        route.name = "local/sp_test"
        route.filter_chain = {
            "order": ["system_prompt"],
            "filters": {
                "system_prompt": {
                    "enabled": True,
                    "prompt": "/nothink reply in french",
                    "override": False,
                }
            },
        }
        route.summary_enabled = False
        route.ctx_len = 131072
        route.max_tokens = 8192
        route.upstream_url = "http://fake:9999"
        route.fallback_chain = []

        sent_payloads = []

        async def mock_post(url, json=None, headers=None, **kwargs):
            sent_payloads.append(copy.deepcopy(json))
            resp = MagicMock()
            resp.status_code = 200
            resp.json = MagicMock(return_value={
                "choices": [{
                    "message": {"content": "OK", "role": "assistant"},
                    "finish_reason": "stop",
                    "index": 0,
                }],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            })
            return resp

        client = MagicMock()
        client.post = mock_post

        result = await process_non_streaming_request(
            url="http://fake:9999/v1/chat/completions",
            client=client,
            payload=payload,
            route_headers={},
            req_id="test123",
            upstream_model="qwen",
            fallback_attempts=[],
            visited_models=set(),
            t_start=0.0,
            route_name="local/sp_test",
            route=route,
        )

        assert result.status_code == 200, f"Got {result.status_code}"
        assert len(sent_payloads) >= 1, "No upstream call captured"

        msgs = sent_payloads[0].get("messages", [])
        assert len(msgs) >= 2, f"Got {len(msgs)} messages: {json.dumps(msgs, indent=2)[:500]}"
        assert msgs[0]["role"] == "system", f"First msg is {msgs[0].get('role')!r}"
        assert "/nothink reply in french" in msgs[0]["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
