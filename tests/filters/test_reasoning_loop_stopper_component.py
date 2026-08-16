"""
E2E test: RLS (Reasoning Loop Stopper) — rileva e blocca loop di reasoning.

Non-streaming path: process_response via FilterChain con fake backend upstream.
"""

import asyncio
import pytest

from tests.e2e.conftest import fake_backend_server  # noqa: F401

_SAME_REASONING = "The user wants me to fix the footer. Let me search for the file..."
_DIFFERENT_REASONING = "Now let me also update the cache after the footer change."


@pytest.fixture
def fake_backend(fake_backend_server):
    """Use the dynamically allocated canonical fake backend."""
    return fake_backend_server.base_url


class _MockResponse:
    """Minimal response protocol mock for RLS testing."""
    def __init__(self, content="", reasoning_content="", model="test-model",
                 finish_reason=None, usage=None):
        self.content = content or ""
        self.reasoning_content = reasoning_content
        self.model = model
        self.finish_reason = finish_reason or "stop"
        self.usage = usage


class TestRLSEndToEnd:

    def test_passes_through_normal_response(self, fake_backend):
        """No reasoning → nessun intervento."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.reasoning_loop_stopper.request import ReasoningLoopStopperFilter

        f = ReasoningLoopStopperFilter({"enabled": True, "upstream_url": fake_backend})
        chain = FilterChain(filters=[f], execution_order=["reasoning_loop_stopper"])

        ctx = FilterExecutionContext(req_id="rls_test", upstream_model="test-model", upstream_url=fake_backend)
        ctx.metadata["conversation_history"] = [{"role": "user", "content": "Hello"}]
        ctx.metadata["upstream_url"] = fake_backend
        ctx.metadata["upstream_model"] = "test-model"

        resp = _MockResponse("Normal response", reasoning_content="")
        result = asyncio.run(chain.process_response(resp, ctx))
        assert result is resp, "Should pass through without reasoning"

    def test_different_reasoning_passes_through(self, fake_backend):
        """Reasoning diverso dal precedente → passa."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.reasoning_loop_stopper.request import ReasoningLoopStopperFilter

        f = ReasoningLoopStopperFilter({"enabled": True, "upstream_url": fake_backend})
        chain = FilterChain(filters=[f], execution_order=["reasoning_loop_stopper"])

        ctx = FilterExecutionContext(req_id="rls_test", upstream_model="test-model", upstream_url=fake_backend)
        ctx.metadata["conversation_history"] = [
            {"role": "assistant", "content": None,
             "reasoning_content": _DIFFERENT_REASONING},
        ]
        ctx.metadata["upstream_url"] = fake_backend
        ctx.metadata["upstream_model"] = "test-model"

        resp = _MockResponse("Response", reasoning_content=_SAME_REASONING)
        result = asyncio.run(chain.process_response(resp, ctx))
        assert result is resp, "Different reasoning should pass through"

    def test_same_reasoning_detected_as_loop(self, fake_backend):
        """Stesso identico reasoning del turno precedente → loop."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.reasoning_loop_stopper.request import ReasoningLoopStopperFilter

        # Configure fake backend to return content on retry
        import httpx
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "chat": {
                    "content": "I'll take a different approach. Let me check the CSS instead.",
                    "include_usage": False,
                },
            }
        }, timeout=5)

        f = ReasoningLoopStopperFilter({
            "enabled": True, "max_repeats": 1, "max_retries": 1,
            "upstream_url": fake_backend,
        })
        chain = FilterChain(filters=[f], execution_order=["reasoning_loop_stopper"])

        ctx = FilterExecutionContext(req_id="rls_test", upstream_model="test-model", upstream_url=fake_backend)
        ctx.metadata["conversation_history"] = [
            {"role": "user", "content": "Fix footer"},
            {"role": "assistant", "content": None,
             "reasoning_content": _SAME_REASONING,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "search", "arguments": '{"q":"footer"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "Result: footer.html"},
        ]
        ctx.metadata["upstream_url"] = fake_backend
        ctx.metadata["upstream_model"] = "test-model"

        resp = _MockResponse("", reasoning_content=_SAME_REASONING)
        result = asyncio.run(chain.process_response(resp, ctx))
        assert result is not resp, "Loop should trigger intervention"
        assert "different approach" in result.content, \
            f"Expected retry content, got: {result.content[:100]}"

    def test_no_previous_reasoning_passes(self, fake_backend):
        """Nessun reasoning precedente in cronologia → passa."""
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.reasoning_loop_stopper.request import ReasoningLoopStopperFilter

        f = ReasoningLoopStopperFilter({"enabled": True, "upstream_url": fake_backend})
        chain = FilterChain(filters=[f], execution_order=["reasoning_loop_stopper"])

        ctx = FilterExecutionContext(req_id="rls_test", upstream_model="test-model", upstream_url=fake_backend)
        ctx.metadata["conversation_history"] = [
            {"role": "user", "content": "Do something"},
        ]
        ctx.metadata["upstream_url"] = fake_backend
        ctx.metadata["upstream_model"] = "test-model"

        resp = _MockResponse("Response", reasoning_content=_SAME_REASONING)
        result = asyncio.run(chain.process_response(resp, ctx))
        assert result is resp, "First reasoning should pass through"

    def test_retry_propagates_reasoning_content(self, fake_backend):
        """RLS retry deve propagare reasoning_content dalla risposta retry."""
        import httpx
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.reasoning_loop_stopper.request import ReasoningLoopStopperFilter

        # Configura il fake backend: il retry deve restituire reasoning_content
        retry_reasoning = "Let me take a completely different approach..."
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "chat": {
                    "content": "Different approach. Searching for the file elsewhere.",
                    "reasoning_content": retry_reasoning,
                    "include_usage": False,
                },
            }
        }, timeout=5)

        f = ReasoningLoopStopperFilter({
            "enabled": True, "max_repeats": 1, "max_retries": 1,
            "upstream_url": fake_backend,
        })
        chain = FilterChain(filters=[f], execution_order=["reasoning_loop_stopper"])

        ctx = FilterExecutionContext(req_id="rls_test", upstream_model="test-model", upstream_url=fake_backend)
        ctx.metadata["conversation_history"] = [
            {"role": "user", "content": "Fix footer"},
            {"role": "assistant", "content": None,
             "reasoning_content": _SAME_REASONING,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "search", "arguments": '{"q":"footer"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "Result: footer.html"},
        ]
        ctx.metadata["upstream_url"] = fake_backend
        ctx.metadata["upstream_model"] = "test-model"

        resp = _MockResponse("", reasoning_content=_SAME_REASONING)
        result = asyncio.run(chain.process_response(resp, ctx))
        assert result is not resp, "Loop should trigger intervention"
        assert result.reasoning_content == retry_reasoning, (
            f"Expected reasoning_content '{retry_reasoning}', got '{result.reasoning_content}'"
        )
        assert "Different approach" in result.content, (
            f"Expected retry content, got: {result.content[:100]}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
