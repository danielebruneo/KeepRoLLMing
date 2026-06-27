"""
Integration test: verify SystemPromptFilter actually modifies the request
that reaches the upstream model.
"""

import asyncio
import pytest
from keeprollming.orchestrator.filters.system_prompt_filter import SystemPromptFilter
from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext


class _TestRequest:
    def __init__(self, messages, model="test", stream=False):
        self.messages = list(messages)
        self.model = model
        self.stream = stream


def _make_ctx(req_id="test123", upstream_model="local/deep", upstream_url="http://test:7001"):
    ctx = FilterExecutionContext(
        req_id=req_id,
        upstream_payload={"messages": [{"role": "user", "content": "hello"}]},
        route_name="test",
        upstream_model=upstream_model,
        upstream_url=upstream_url,
    )
    return ctx


class TestFilterChainProcessRequest:
    @pytest.mark.asyncio
    async def test_system_prompt_inserted_by_filter_chain(self):
        """System prompt is injected when filter chain runs process_request."""
        chain = FilterChain.from_route_config({
            "order": ["system_prompt"],
            "filters": {
                "system_prompt": {"enabled": True, "prompt": "Be helpful", "override": False},
            },
        })
        req = _TestRequest([{"role": "user", "content": "Hello"}])
        ctx = _make_ctx()
        result = await chain.process_request(req, ctx)
        assert result.messages[0]["role"] == "system"
        assert result.messages[0]["content"] == "Be helpful"

    @pytest.mark.asyncio
    async def test_system_prompt_preserves_existing_messages(self):
        """Existing messages are preserved after system prompt injection."""
        chain = FilterChain.from_route_config({
            "order": ["system_prompt"],
            "filters": {
                "system_prompt": {"enabled": True, "prompt": "X", "override": False},
            },
        })
        req = _TestRequest([{"role": "user", "content": "Hello"}])
        ctx = _make_ctx()
        result = await chain.process_request(req, ctx)
        assert result.messages[-1]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_disabled_filter_passes_through(self):
        """Disabled filter does not modify messages."""
        chain = FilterChain.from_route_config({
            "order": ["system_prompt"],
            "filters": {
                "system_prompt": {"enabled": False, "prompt": "X"},
            },
        })
        req = _TestRequest([{"role": "user", "content": "Hello"}])
        ctx = _make_ctx()
        result = await chain.process_request(req, ctx)
        assert result.messages == req.messages

    @pytest.mark.asyncio
    async def test_override_replaces_system_prompt(self):
        """Override=True replaces existing system prompt."""
        chain = FilterChain.from_route_config({
            "order": ["system_prompt"],
            "filters": {
                "system_prompt": {"enabled": True, "prompt": "NEW", "override": True},
            },
        })
        req = _TestRequest([
            {"role": "system", "content": "OLD system"},
            {"role": "user", "content": "Hello"},
        ])
        ctx = _make_ctx()
        result = await chain.process_request(req, ctx)
        assert result.messages[0]["content"] == "NEW"
        assert "OLD" not in result.messages[0]["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
