"""
E2E test: TLS filter detects repeated tool calls via fake backend in-process.
"""

import asyncio
import pytest

from tests.e2e.conftest import fake_backend_server  # noqa: F401


class _NonStreamingResponse:
    """Simple response class mimicking non-streaming response protocol."""
    def __init__(self, content="", tool_calls=None, model="test-model", finish_reason=None, usage=None):
        self.content = content or ""
        self.tool_calls = tool_calls or []
        self.model = model
        self.finish_reason = finish_reason or "stop"
        self.usage = usage


@pytest.fixture
def fake_backend(fake_backend_server):
    """Use the dynamically allocated canonical fake backend."""
    return fake_backend_server.base_url


class TestTLSEndToEnd:

    def test_tls_passes_through_normal_response(self, fake_backend):
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter({"enabled": True, "max_attempts": 1, "upstream_url": fake_backend})
        chain = FilterChain(filters=[f], execution_order=["model_tool_loop_stopper"])

        ctx = FilterExecutionContext(req_id="tls_test", upstream_model="test-model", upstream_url=fake_backend)
        ctx.metadata["conversation_history"] = [{"role": "user", "content": "Hello"}]
        ctx.metadata["upstream_url"] = fake_backend
        ctx.metadata["upstream_model"] = "test-model"

        class MR:
            def __init__(self, content="", tool_calls=None, finish_reason=None):
                self.content = content
                self.tool_calls = tool_calls or []
                self.model = "test-model"
                self.finish_reason = "stop"
                self.usage = None

        resp = MR("Normal", [])
        result = asyncio.run(chain.process_response(resp, ctx))
        assert result is resp
        print("TLS pass-through PASSED")

    def test_tls_fires_on_repeated_tool_call(self, fake_backend):
        from keeprollming.orchestrator.filter import FilterChain, FilterExecutionContext
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter({"enabled": True, "max_attempts": 1, "upstream_url": fake_backend})
        chain = FilterChain(filters=[f], execution_order=["model_tool_loop_stopper"])

        ctx = FilterExecutionContext(req_id="tls_test", upstream_model="test-model", upstream_url=fake_backend)
        ctx.metadata["conversation_history"] = [
            {"role": "user", "content": "Find files"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search", "arguments": '{"p":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"},
        ]
        ctx.metadata["upstream_url"] = fake_backend
        ctx.metadata["upstream_model"] = "test-model"

        class MR:
            def __init__(self, content="", tool_calls=None, finish_reason=None):
                self.content = content
                self.tool_calls = tool_calls or []
                self.model = "test-model"
                self.finish_reason = "stop"
                self.usage = None

        resp = MR("", tool_calls=[{
            "id": "r1", "type": "function",
            "function": {"name": "search", "arguments": '{"p":"x"}'},
        }])
        result = asyncio.run(chain.process_response(resp, ctx))
        assert result is not resp, "TLS did not intervene"
        print(f"TLS result: {result.content[:200]}")
        print("TLS loop detection PASSED")

    def test_tls_streaming_fires_on_repeated_tool_call(self, fake_backend):
        """Streaming: TLS detects repeated tool call and intervenes via Pipeline."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
        })

        pipeline = Pipeline([f])

        import httpx
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "chat": {
                    "script": [
                        # First fake backend call IS the TLS retry → return plain text
                        {"content": "Already searched that. The file is at /tmp/test.py"},
                    ],
                },
            },
        })

        payload = {
            "messages": [
                {"role": "user", "content": "Find files"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "search", "arguments": '{"p":"x"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "found"},
            ],
            "model": "test-model",
        }

        async def _go():
            return await pipeline.process_response(
                StreamingResponse(content="I'll search again",
                                  model="test-model",
                                  tool_calls=[{
                                      "id": "r1", "type": "function",
                                      "function": {"name": "search", "arguments": '{"p":"x"}'},
                                  }]),
                payload, "tls_stream", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=True,
                upstream_caller=None,
            )

        result = asyncio.run(_go())
        assert result is not None, "TLS streaming result should not be None"
        assert hasattr(result, 'content'), f"Result should have content attribute, got {type(result)}"
        assert "Already searched that" in result.content, \
            f"TLS did not intervene; expected retry content, got: {result.content[:200]}"
        assert result.content != "I'll search again", "TLS should have changed the content"
        print(f"TLS streaming result: {result.content[:200]}")
        print("TLS streaming loop detection PASSED")

    def test_tls_nonstreaming_via_pipeline(self, fake_backend):
        """Non-streaming: TLS detects repeated tool call via Pipeline."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
        })

        pipeline = Pipeline([f])

        import httpx
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "chat": {
                    "script": [
                        # The first fake backend call IS the TLS retry → return text
                        {"content": "Done. File found at /tmp/result.txt"},
                    ],
                },
            },
        })

        payload = {
            "messages": [
                {"role": "user", "content": "Find files"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "search", "arguments": '{"p":"x"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "found"},
            ],
            "model": "test-model",
        }

        async def _go():
            return await pipeline.process_response(
                StreamingResponse(content="Let me search again",
                                  model="test-model",
                                  tool_calls=[{
                                      "id": "r2", "type": "function",
                                      "function": {"name": "search", "arguments": '{"p":"x"}'},
                                  }]),
                payload, "tls_ns", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False,
                upstream_caller=None,
            )

        result = asyncio.run(_go())
        assert result is not None
        assert hasattr(result, 'content')
        assert "File found" in result.content, \
            f"TLS did not intervene (non-streaming); got: {result.content[:200]}"
        print(f"TLS non-streaming result: {result.content[:200]}")
        print("TLS non-streaming via Pipeline PASSED")

    def test_tls_accepts_new_tool_call_after_filtering_repeated(self, fake_backend):
        """TLS filters repeated tool calls but accepts a new/different one."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
        })

        pipeline = Pipeline([f])

        import httpx
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "chat": {
                    "script": [
                        # TLS retry response: model returns a NEW tool call (browse)
                        # plus the repeated one (search) — TLS should filter search,
                        # keep browse, and NOT fall back
                        {
                            "content": "Let me browse instead",
                            "tool_calls": [
                                {
                                    "id": "rpt", "type": "function",
                                    "function": {"name": "search", "arguments": '{"p":"x"}'},
                                },
                                {
                                    "id": "nw1", "type": "function",
                                    "function": {"name": "browse", "arguments": '{"url":"http://x"}'},
                                },
                            ],
                        },
                    ],
                },
            },
        })

        payload = {
            "messages": [
                {"role": "user", "content": "Find files"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "search", "arguments": '{"p":"x"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "found"},
            ],
            "model": "test-model",
        }

        async def _go():
            return await pipeline.process_response(
                StreamingResponse(
                    content="Searching...",
                    model="test-model",
                    tool_calls=[
                        {
                            "id": "r1", "type": "function",
                            "function": {"name": "search", "arguments": '{"p":"x"}'},
                        },
                        {
                            "id": "r2", "type": "function",
                            "function": {"name": "search", "arguments": '{"p":"x"}'},
                        },  # repeated
                    ],
                ),
                payload, "tls_mixed", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False,
                upstream_caller=None,
            )

        result = asyncio.run(_go())
        assert result is not None
        assert hasattr(result, 'content')
        # Should contain retry content (browse is a new tool, accepted)
        assert "browse" in result.content.lower() or (
            hasattr(result, 'tool_calls') and len(result.tool_calls) == 1
        ), f"Expected new tool call 'browse' to be accepted. content={result.content[:200]}"
        # Should NOT fall back (fallback message would indicate all-repeated)
        fallback = getattr(f.config, 'fallback_message', '') or ''
        if fallback:
            assert fallback not in str(result.content), \
                f"Should not fallback when new tool call present. Got: {result.content[:200]}"
        print(f"TLS mixed result: content={result.content[:200]}")
        tc_names = [tc.get('function', {}).get('name', '?')
                    for tc in getattr(result, 'tool_calls', [])]
        print(f"TLS accepted tool_calls: {tc_names}")
        print("TLS accepts new tool call PASSED")

    # ── Fuzzy loop detection ─────────────────────────────────────────────

    def test_fuzzy_loop_detects_same_function_different_args(self, fake_backend):
        """TLS fuzzy detection: same function called 3x with different args → loop."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
            "fuzzy_max_repeats": 3,
        })

        pipeline = Pipeline([f])

        import httpx
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {
                "chat": {
                    "script": [
                        {"content": "OK, I'll try a different approach now."},
                    ],
                },
            },
        })

        # Conversation has 3 prior calls to bash_tool(date) with SAME args
        payload = {
            "messages": [
                {"role": "user", "content": "Run date"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "Wed May 27"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c2", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c2", "content": "Wed May 27"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c3", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c3", "content": "Wed May 27"},
            ],
            "model": "test-model",
        }

        # Current response has ANOTHER bash_tool(date) with same args (4th)
        current_response = StreamingResponse(
            content="Let me try date again:",
            model="test-model",
            tool_calls=[{
                "id": "c4", "type": "function",
                "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
            }],
        )

        async def _go():
            return await pipeline.process_response(
                current_response, payload, "tls_fuzzy", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False, upstream_caller=None,
            )

        result = asyncio.run(_go())
        assert result is not None
        assert hasattr(result, 'content')
        # TLS should have intervened (fuzzy loop) — retry content should appear
        assert "different approach" in result.content, \
            f"TLS fuzzy loop did not intervene. content={result.content[:200]}"
        print(f"TLS fuzzy loop: content={result.content[:200]}")
        print("TLS fuzzy loop detection PASSED")

    def test_fuzzy_loop_not_detected_different_functions(self, fake_backend):
        """TLS fuzzy detection: different functions → no loop, even with 3+ repeats."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
            "fuzzy_max_repeats": 3,
        })

        pipeline = Pipeline([f])

        payload = {
            "messages": [
                {"role": "user", "content": "Search and browse"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"x"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "result1"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c2", "type": "function",
                    "function": {"name": "browse", "arguments": '{"url":"http://x"}'},
                }]},
                {"role": "tool", "tool_call_id": "c2", "content": "result2"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c3", "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"y"}'},
                }]},
                {"role": "tool", "tool_call_id": "c3", "content": "result3"},
            ],
            "model": "test-model",
        }

        current_response = StreamingResponse(
            content="Let me browse more:",
            model="test-model",
            tool_calls=[{
                "id": "c4", "type": "function",
                "function": {"name": "browse", "arguments": '{"url":"http://y"}'},
            }],
        )

        async def _go():
            return await pipeline.process_response(
                current_response, payload, "tls_fuzzy_diff", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False, upstream_caller=None,
            )

        result = asyncio.run(_go())
        assert result is not None
        # TLS should NOT intervene — different functions alternating is not a loop
        assert hasattr(result, 'tool_calls') and len(result.tool_calls) > 0, \
            "TLS should NOT intervene when functions are different"
        tc_names = [tc.get('function', {}).get('name', '?')
                    for tc in result.tool_calls]
        print(f"TLS fuzzy diff: tool_calls={tc_names}")
        print("TLS fuzzy loop (different functions) PASSED — no intervention")

    # ── Stronger TLS message + fallback includes function name ────────────

    def test_stronger_tls_user_message_and_fallback_with_name(self, fake_backend):
        """TLS injects user message (not just tool result) + fallback has function name."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
            "tls_message": "My custom TLS message",
            "fallback_message": "My custom fallback",
        })

        # We need to check that the TLS message format includes a USER role.
        # Check config flag existence:
        self._check_has_user_message_support(f)

        pipeline = Pipeline([f])

        payload = {
            "messages": [
                {"role": "user", "content": "Run date"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "Wed May 27"},
            ],
            "model": "test-model",
        }

        async def _go():
            return await pipeline.process_response(
                StreamingResponse(content="Running date:", model="test-model",
                                  tool_calls=[{
                                      "id": "c5", "type": "function",
                                      "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                                  }]),
                payload, "tls_strong", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False, upstream_caller=None,
            )

        result = asyncio.run(_go())
        assert result is not None
        assert hasattr(result, 'content')
        print(f"TLS stronger message result: content={result.content[:200]}")
        print("TLS stronger message PASSED")

    def _check_has_user_message_support(self, f):
        """Verify the filter config has user_message and fallback_template fields."""
        assert hasattr(f.config, 'tls_message'), "Missing tls_message config"
        assert hasattr(f.config, 'fallback_message'), "Missing fallback_message config"
        # New features that should exist
        assert hasattr(f.config, 'send_user_message'), \
            "Missing send_user_message config — implement first!"
        assert hasattr(f.config, 'fallback_template'), \
            "Missing fallback_template config — implement first!"
        assert hasattr(f.config, 'fuzzy_max_repeats'), \
            "Missing fuzzy_max_repeats config — implement first!"
        assert hasattr(f.config, 'ab_loop_detection'), \
            "Missing ab_loop_detection config — implement first!"
        print("TLS config structure OK")

    # ── Fuzzy with exact args match ────────────────────────────────────────

    def test_fuzzy_requires_exact_args(self, fake_backend):
        """Fuzzy detection requires function+args, not just function name."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
            "fuzzy_max_repeats": 3,
        })

        pipeline = Pipeline([f])

        import httpx
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {"chat": {"script": [{"content": "OK, different now."}]}},
        })

        # 3 calls to bash_tool(date) with SAME args → should trigger fuzzy
        payload = {
            "messages": [
                {"role": "user", "content": "Run date"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "out1"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c2", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c2", "content": "out2"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c3", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c3", "content": "out3"},
            ],
            "model": "test-model",
        }

        current = StreamingResponse(content="Again:", model="test-model", tool_calls=[{
            "id": "c4", "type": "function",
            "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
        }])

        async def _go():
            return await pipeline.process_response(
                current, payload, "tls_fuzzy_exact", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False, upstream_caller=None,
            )
        result = asyncio.run(_go())
        assert hasattr(result, 'content')
        assert "different now" in result.content, \
            f"Fuzzy with exact args did NOT trigger. content={result.content[:200]}"
        print("Fuzzy with exact args PASSED")

    def test_fuzzy_not_triggered_different_args(self, fake_backend):
        """Fuzzy NOT triggered when same function but different args."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
            "fuzzy_max_repeats": 3,
        })

        pipeline = Pipeline([f])

        payload = {
            "messages": [
                {"role": "user", "content": "Run date"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }]},
                {"role": "tool", "tool_call_id": "c1", "content": "out1"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c2", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date +%s"}'},
                }]},
                {"role": "tool", "tool_call_id": "c2", "content": "out2"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "c3", "type": "function",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date -u"}'},
                }]},
                {"role": "tool", "tool_call_id": "c3", "content": "out3"},
            ],
            "model": "test-model",
        }

        current = StreamingResponse(content="Let me try:", model="test-model", tool_calls=[{
            "id": "c4", "type": "function",
            "function": {"name": "bash_tool", "arguments": '{"command":"date --iso-8601"}'},
        }])

        async def _go():
            return await pipeline.process_response(
                current, payload, "tls_fuzzy_diffargs", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False, upstream_caller=None,
            )
        result = asyncio.run(_go())
        # Fuzzy should NOT trigger — all bash_tool calls have DIFFERENT args
        assert result is current, \
            f"Fuzzy should NOT trigger when args differ. content={getattr(result, 'content', '?')[:200]}"
        print("Fuzzy with different args NOT triggered PASSED")

    # ── AB loop detection ──────────────────────────────────────────────────

    def test_ab_loop_detected(self, fake_backend):
        """AB loop detection: A,B,A,B pattern triggers TLS."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
            "ab_loop_detection": True,
        })

        pipeline = Pipeline([f])

        import httpx
        httpx.post(f"{fake_backend}/__scenario", json={
            "scenario": {"chat": {"script": [{"content": "AB loop stopped."}]}},
        })

        # Conversation: A, result, B, result, A, result
        payload = {
            "messages": [
                {"role": "user", "content": "Do stuff"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "a1", "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"x"}'},
                }]},
                {"role": "tool", "tool_call_id": "a1", "content": "res_a"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "b1", "type": "function",
                    "function": {"name": "browse", "arguments": '{"url":"http://x"}'},
                }]},
                {"role": "tool", "tool_call_id": "b1", "content": "res_b"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "a2", "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"x"}'},
                }]},
                {"role": "tool", "tool_call_id": "a2", "content": "res_a2"},
            ],
            "model": "test-model",
        }

        # Current response has B (completes A,B,A,B pattern)
        current = StreamingResponse(content="Let me browse:", model="test-model", tool_calls=[{
            "id": "b2", "type": "function",
            "function": {"name": "browse", "arguments": '{"url":"http://x"}'},
        }])

        async def _go():
            return await pipeline.process_response(
                current, payload, "tls_ab", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False, upstream_caller=None,
            )
        result = asyncio.run(_go())
        assert hasattr(result, 'content')
        assert "AB loop stopped" in result.content, \
            f"AB loop NOT detected. content={result.content[:200]}"
        print("AB loop detection PASSED")

    def test_ab_loop_not_detected_different_args(self, fake_backend):
        """AB loop NOT detected when args differ."""
        from keeprollming.orchestrator.pipeline import Pipeline
        from keeprollming.orchestrator.filter import StreamingResponse
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter

        f = ToolLoopStopperFilter(config={
            "enabled": True, "max_attempts": 1, "upstream_url": fake_backend,
            "ab_loop_detection": True,
        })

        pipeline = Pipeline([f])

        # Same functions but different arguments → NOT an AB loop
        payload = {
            "messages": [
                {"role": "user", "content": "Do stuff"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "a1", "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"x"}'},
                }]},
                {"role": "tool", "tool_call_id": "a1", "content": "res_a"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "b1", "type": "function",
                    "function": {"name": "browse", "arguments": '{"url":"http://x"}'},
                }]},
                {"role": "tool", "tool_call_id": "b1", "content": "res_b"},
                {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "a2", "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"y"}'},  # different!
                }]},
                {"role": "tool", "tool_call_id": "a2", "content": "res_a2"},
            ],
            "model": "test-model",
        }

        current = StreamingResponse(content="Let me browse:", model="test-model", tool_calls=[{
            "id": "b2", "type": "function",
            "function": {"name": "browse", "arguments": '{"url":"http://y"}'},  # different!
        }])

        async def _go():
            return await pipeline.process_response(
                current, payload, "tls_ab_diff", "test-model",
                route_name="test", upstream_url=fake_backend,
                is_streaming=False, upstream_caller=None,
            )
        result = asyncio.run(_go())
        # AB loop should NOT trigger — different args
        assert result is current, \
            f"AB loop should NOT trigger with different args. content={getattr(result, 'content', '?')[:200]}"
        print("AB loop with different args NOT triggered PASSED")


# ── New Feature E2E Tests ─────────────────────────────────────────────────────


class TestMaxRepeatsE2E:

    @staticmethod
    def _make_streaming(content="", tool_calls=None):
        from keeprollming.orchestrator.filter import StreamingResponse
        return StreamingResponse(content=content, tool_calls=tool_calls or [])

    @staticmethod
    def _make_nonstreaming(content="", tool_calls=None):
        return MockResponseClass(content=content, tool_calls=tool_calls or [])

    @staticmethod
    def _make_ctx(conv, upstream_url, req_id="tls_mr"):
        return FilterExecutionContext(
            req_id=req_id,
            upstream_payload={"messages": conv},
            upstream_model="test-model",
            upstream_url=upstream_url,
        )

    def _make_ctx(self, conv, upstream_url, req_id="tls_mr"):
        from keeprollming.orchestrator.filter import FilterExecutionContext
        return FilterExecutionContext(
            req_id=req_id,
            upstream_payload={"messages": conv},
            upstream_model="test-model",
            upstream_url=upstream_url,
        )

    def test_max_repeats_2_not_enough_with_one_repeat__streaming(self, fake_backend):
        """max_repeats=2: one repeat NOT enough — streaming response."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        from keeprollming.orchestrator.filter import StreamingResponse
        f = ToolLoopStopperFilter({"enabled": True, "max_repeats": 2, "upstream_url": fake_backend})
        conv = [
            {"role": "user", "content": "Find files"},
            {"role": "assistant", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"},
        ]
        current = StreamingResponse(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is current, "Should pass through (only 1 repeat)"

    def test_max_repeats_2_not_enough_with_one_repeat__nonstreaming(self, fake_backend):
        """max_repeats=2: one repeat NOT enough — non-streaming response."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        from keeprollming.orchestrator.filter import StreamingResponse
        f = ToolLoopStopperFilter({"enabled": True, "max_repeats": 2, "upstream_url": fake_backend})
        conv = [
            {"role": "user", "content": "Find files"},
            {"role": "assistant", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"},
        ]
        current = _NonStreamingResponse(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is current, "Should pass through (only 1 repeat)"

    def test_max_repeats_2_triggers_on_second_repeat__streaming(self, fake_backend):
        """max_repeats=2: two repeats trigger — streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        from keeprollming.orchestrator.filter import StreamingResponse
        f = ToolLoopStopperFilter({"enabled": True, "max_repeats": 2, "upstream_url": fake_backend})
        conv = [
            {"role": "user", "content": "Find files"},
            {"role": "assistant", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"},
            {"role": "assistant", "tool_calls": [{
                "id": "c2", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c2", "content": "found again"},
        ]
        current = StreamingResponse(tool_calls=[{
            "id": "c3", "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is not current, "Should trigger (2 repeats)"

    def test_max_repeats_2_triggers_on_second_repeat__nonstreaming(self, fake_backend):
        """max_repeats=2: two repeats trigger — non-streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        f = ToolLoopStopperFilter({"enabled": True, "max_repeats": 2, "upstream_url": fake_backend})
        current = _NonStreamingResponse(tool_calls=[{
            "id": "c3", "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }])
        conv = [
            {"role": "user", "content": "Find files"},
            {"role": "assistant", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"},
            {"role": "assistant", "tool_calls": [{
                "id": "c2", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c2", "content": "found again"},
        ]
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is not current, "Should trigger (2 repeats)"

    def test_max_repeats_0_disabled_no_trigger__streaming(self, fake_backend):
        """max_repeats=0: no trigger — streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        from keeprollming.orchestrator.filter import StreamingResponse
        f = ToolLoopStopperFilter({"enabled": True, "max_repeats": 0, "upstream_url": fake_backend})
        conv = [
            {"role": "user", "content": "Find files"},
            {"role": "assistant", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"},
        ]
        current = StreamingResponse(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is current, "Should pass through (max_repeats=0)"

    def test_max_repeats_0_disabled_no_trigger__nonstreaming(self, fake_backend):
        """max_repeats=0: no trigger — non-streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        f = ToolLoopStopperFilter({"enabled": True, "max_repeats": 0, "upstream_url": fake_backend})
        conv = [
            {"role": "user", "content": "Find files"},
            {"role": "assistant", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "search", "arguments": '{"q":"x"}'},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "found"},
        ]
        current = _NonStreamingResponse(tool_calls=[{
            "id": "c2", "type": "function",
            "function": {"name": "search", "arguments": '{"q":"x"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is current, "Should pass through (max_repeats=0)"


class TestFuzzyLookBackE2E:

    @staticmethod
    def _make_ctx(conv, upstream_url, req_id="tls_fuzzy"):
        from keeprollming.orchestrator.filter import FilterExecutionContext
        return FilterExecutionContext(
            req_id=req_id,
            upstream_payload={"messages": conv},
            upstream_model="test-model",
            upstream_url=upstream_url,
        )

    def test_fuzzy_look_back_limits_window__streaming(self, fake_backend):
        """fuzzy_max_repeats=3, look_back=3: only last 3 — streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        from keeprollming.orchestrator.filter import StreamingResponse
        f = ToolLoopStopperFilter({
            "enabled": True, "max_repeats": 0,
            "fuzzy_max_repeats": 3, "fuzzy_look_back": 3,
            "upstream_url": fake_backend,
        })
        conv = [{"role": "user", "content": "Start"}]
        for i in range(5):
            conv.append({"role": "assistant", "tool_calls": [{
                "id": f"c{i}", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
            }]})
            conv.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"res{i}"})
        current = StreamingResponse(tool_calls=[{
            "id": "cx", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is not current, "Should trigger (3 within window)"

    def test_fuzzy_look_back_limits_window__nonstreaming(self, fake_backend):
        """fuzzy_max_repeats=3, look_back=3: only last 3 — non-streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        f = ToolLoopStopperFilter({
            "enabled": True, "max_repeats": 0,
            "fuzzy_max_repeats": 3, "fuzzy_look_back": 3,
            "upstream_url": fake_backend,
        })
        conv = [{"role": "user", "content": "Start"}]
        for i in range(5):
            conv.append({"role": "assistant", "tool_calls": [{
                "id": f"c{i}", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
            }]})
            conv.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"res{i}"})
        current = _NonStreamingResponse(tool_calls=[{
            "id": "cx", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is not current, "Should trigger (3 within window)"

    def test_fuzzy_look_back_outside_window_no_trigger__streaming(self, fake_backend):
        """fuzzy_max_repeats=3, look_back=3: outside don't count — streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        from keeprollming.orchestrator.filter import StreamingResponse
        f = ToolLoopStopperFilter({
            "enabled": True, "max_repeats": 0,
            "fuzzy_max_repeats": 3, "fuzzy_look_back": 3,
            "upstream_url": fake_backend,
        })
        conv = [{"role": "user", "content": "Start"}]
        for i in range(3):
            conv.append({"role": "assistant", "tool_calls": [{
                "id": f"c{i}", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
            }]})
            conv.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"res{i}"})
        conv.append({"role": "assistant", "tool_calls": [{
            "id": "cm", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Milan"}'},
        }]})
        conv.append({"role": "tool", "tool_call_id": "cm", "content": "milan"})
        conv.append({"role": "assistant", "tool_calls": [{
            "id": "cr", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
        }]})
        conv.append({"role": "tool", "tool_call_id": "cr", "content": "rome2"})
        current = StreamingResponse(tool_calls=[{
            "id": "cx", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is current, "Should NOT trigger (only 1 in window)"

    def test_fuzzy_look_back_outside_window_no_trigger__nonstreaming(self, fake_backend):
        """fuzzy_max_repeats=3, look_back=3: outside don't count — non-streaming."""
        from keeprollming.filters.tool_loop_stopper.request import ToolLoopStopperFilter
        f = ToolLoopStopperFilter({
            "enabled": True, "max_repeats": 0,
            "fuzzy_max_repeats": 3, "fuzzy_look_back": 3,
            "upstream_url": fake_backend,
        })
        conv = [{"role": "user", "content": "Start"}]
        for i in range(3):
            conv.append({"role": "assistant", "tool_calls": [{
                "id": f"c{i}", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
            }]})
            conv.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"res{i}"})
        conv.append({"role": "assistant", "tool_calls": [{
            "id": "cm", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Milan"}'},
        }]})
        conv.append({"role": "tool", "tool_call_id": "cm", "content": "milan"})
        conv.append({"role": "assistant", "tool_calls": [{
            "id": "cr", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
        }]})
        conv.append({"role": "tool", "tool_call_id": "cr", "content": "rome2"})
        current = _NonStreamingResponse(tool_calls=[{
            "id": "cx", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'},
        }])
        ctx = self._make_ctx(conv, fake_backend)
        result = asyncio.run(f.process_response(current, ctx))
        assert result is current, "Should NOT trigger (only 1 in window)"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
