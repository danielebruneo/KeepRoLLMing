"""
Tests for System Prompt Filter.
"""

import pytest
from keeprollming.filters.system_prompt.request import SystemPromptFilter
from keeprollming.orchestrator.filter import FilterExecutionContext


class MockRequest:
    def __init__(self, messages):
        self.messages = messages
        self.model = "test"
        self.stream = False


def make_context():
    return FilterExecutionContext(req_id="test123")


class TestSystemPromptInsert:
    @pytest.mark.asyncio
    async def test_insert_when_no_system_message(self):
        """When no system message exists, insert one at the beginning."""
        req = MockRequest([{"role": "user", "content": "Hello"}])
        ctx = make_context()
        f = SystemPromptFilter({"enabled": True, "prompt": "Be helpful", "override": False})
        result = await f.process_request(req, ctx)
        assert result.messages[0]["role"] == "system"
        assert result.messages[0]["content"] == "Be helpful"
        assert result.messages[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_prepend_to_existing_system(self):
        """When override=False, prepend to existing system prompt."""
        req = MockRequest([{"role": "system", "content": "You are an AI"}, {"role": "user", "content": "Hi"}])
        ctx = make_context()
        f = SystemPromptFilter({"enabled": True, "prompt": "Be helpful", "override": False})
        result = await f.process_request(req, ctx)
        assert "Be helpful" in result.messages[0]["content"]
        assert "You are an AI" in result.messages[0]["content"]
        # New prompt comes first
        assert result.messages[0]["content"].startswith("Be helpful")

    @pytest.mark.asyncio
    async def test_override_existing_system(self):
        """When override=True, replace the system prompt entirely."""
        req = MockRequest([{"role": "system", "content": "Old system prompt"}, {"role": "user", "content": "Hi"}])
        ctx = make_context()
        f = SystemPromptFilter({"enabled": True, "prompt": "New system prompt", "override": True})
        result = await f.process_request(req, ctx)
        assert result.messages[0]["content"] == "New system prompt"
        assert "Old system prompt" not in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_disabled_does_nothing(self):
        """When disabled, the request passes through unchanged."""
        req = MockRequest([{"role": "user", "content": "Hello"}])
        ctx = make_context()
        f = SystemPromptFilter({"enabled": False, "prompt": "Be helpful"})
        result = await f.process_request(req, ctx)
        assert result.messages == req.messages  # Unchanged

    @pytest.mark.asyncio
    async def test_empty_prompt_does_nothing(self):
        """When prompt is empty, the request passes through unchanged."""
        req = MockRequest([{"role": "user", "content": "Hello"}])
        ctx = make_context()
        f = SystemPromptFilter({"enabled": True, "prompt": ""})
        result = await f.process_request(req, ctx)
        assert result.messages == req.messages


class TestProcessResponse:
    @pytest.mark.asyncio
    async def test_passes_through(self):
        """process_response is a no-op."""
        resp = type('obj', (object,), {'content': 'test', 'model': 'm', 'usage': None, 'finish_reason': 'stop'})()
        ctx = make_context()
        f = SystemPromptFilter({"enabled": True, "prompt": "x"})
        result = await f.process_response(resp, ctx)
        assert result is resp


class TestPriority:
    def test_priority_value(self):
        """System prompt filter runs first (lowest priority)."""
        assert SystemPromptFilter.priority == 10
        from keeprollming.filters.tool_loop_stopper import ToolLoopStopperFilter
        assert SystemPromptFilter.priority < ToolLoopStopperFilter.priority  # 10 < 25
