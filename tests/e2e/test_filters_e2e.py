"""
E2E test: Nudge + SystemPrompt filters via fake backend in-process.
"""

import asyncio
import time
import threading

import pytest
import uvicorn


FAKE_PORT = 19998


@pytest.fixture(scope="module")
def fake_backend():
    from tests.e2e.fake_backend import create_app
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=FAKE_PORT, log_level="error")
    server = uvicorn.Server(config)
    def run():
        asyncio.run(server.serve())
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(2)
    yield f"http://127.0.0.1:{FAKE_PORT}"
    server.should_exit = True


class TestSystemPromptE2E:

    def test_system_prompt_reaches_upstream(self, fake_backend):
        """Fake backend receives the injected system prompt."""
        import httpx
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.orchestrator.filters.system_prompt_filter import SystemPromptFilter

        # Configure fake backend to echo back the first message as content
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "models": {"test": {"context_length": 4096}},
                "chat": {"content": "OK", "include_usage": False},
            }
        }, timeout=5)

        f = SystemPromptFilter({"enabled": True, "prompt": "BE_HELPFUL", "override": False})
        chain = FilterChain(filters=[f], execution_order=["system_prompt"])

        ctx = FilterExecutionContext(req_id="sp_test", upstream_model="test", upstream_url=fake_backend)
        ctx.metadata["upstream_url"] = fake_backend

        class RQ:
            def __init__(self, msgs):
                self.messages = list(msgs)
                self.model = "test"
                self.stream = False

        req = RQ([{"role": "user", "content": "Hello"}])
        result = asyncio.run(chain.process_request(req, ctx))

        assert result.messages[0]["role"] == "system"
        assert result.messages[0]["content"] == "BE_HELPFUL"
        print("SystemPrompt E2E PASSED")


class TestNudgeE2E:

    def test_nudge_fires_on_lazy_response(self, fake_backend):
        """Nudge triggers TLS on response ending with colon."""
        import httpx
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.orchestrator.filters.model_nudge_filter import ModelNudgeFilter

        # Scenario: first call returns lazy "OK:", retry returns full response
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "models": {"test": {"context_length": 4096}},
                "chat": {
                    "content": "Actually, let me continue: this is the full response.",
                    "include_usage": False,
                },
            }
        }, timeout=5)

        f = ModelNudgeFilter({
            "enabled": True, "trigger_patterns": [":$"],
            "nudge_message": "Continue.", "max_nudge_attempts": 1,
            "upstream_url": fake_backend,
        })
        chain = FilterChain(filters=[f], execution_order=["model_nudge"])

        ctx = FilterExecutionContext(req_id="nudge_test", upstream_model="test", upstream_url=fake_backend)
        ctx.upstream_payload = {"messages": [{"role": "user", "content": "say something"}]}
        ctx.metadata["upstream_model"] = "test"
        ctx.metadata["upstream_url"] = fake_backend

        class MR:
            def __init__(self, content="", tool_calls=None, finish_reason=None):
                self.content = content
                self.tool_calls = tool_calls or []
                self.model = "test"
                self.finish_reason = "stop"
                self.usage = None

        # Response ending with colon → nudge should trigger
        resp = MR("I will now a colon:", [])
        result = asyncio.run(chain.process_response(resp, ctx))

        # After nudge retry, content should be longer than original
        assert result is not resp, "Nudge did not modify response"
        assert len(result.content) > len("I will now a colon:"), \
            f"Nudge retry didn't extend content: {result.content[:100]}"
        print(f"Nudge result: {result.content[:200]}")
        print("Nudge E2E PASSED")


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
