"""Tests for execution event emission helpers (O7 migration).

Verifies that ``events_execution`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""


from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_execution import (
    emit_assistant,
    emit_conversation,
    emit_derived_performance_metrics,
    emit_execution_event,
    emit_failed,
    emit_fallback,
    emit_fallback_chain,
    emit_http_in,
    emit_http_out,
    emit_invalid_upstream,
    emit_missing_upstream,
    emit_override,
    emit_performance_metrics,
    emit_pipeline_error,
    emit_repacked,
    emit_request_received,
    emit_request_route,
    emit_request_start,
    emit_route_not_found,
    emit_route_resolved,
    emit_strip_image_done,
    emit_strip_image_error,
    emit_strip_image_failed,
    emit_strip_image_retry,
    emit_timeout,
    emit_tool_call,
    emit_tool_result,
    emit_upstream_error,
)


class TestEmitExecutionEvent:
    """Test the core emit_execution_event helper."""

    def test_emits_without_dispatcher(self):
        """Event is created and returned even when no dispatcher is available."""
        event = emit_execution_event("req-1", "execution.chat.http_in")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.chat.http_in"
        assert event.source == EventSource(domain="execution", component="chat")
        assert event.req_id == "req-1"
        assert event.level == "BASIC"

    def test_emits_to_dispatcher(self):
        """Event is emitted through the dispatcher when provided."""
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []

        def capture(event: RuntimeEvent) -> None:
            emitted_events.append(event)

        dispatcher.subscribe("execution", capture)
        event = emit_execution_event(
            "req-2", "execution.chat.http_in", dispatcher=dispatcher
        )
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1
        assert emitted_events[0].type == "execution.chat.http_in"
        assert emitted_events[0].req_id == "req-2"

    def test_no_op_with_none_dispatcher(self):
        """Passing None as dispatcher is a no-op (no exception)."""
        event = emit_execution_event("req-3", "execution.chat.http_in", dispatcher=None)
        assert isinstance(event, RuntimeEvent)

    def test_custom_level(self):
        """Events can be emitted with non-default levels."""
        event = emit_execution_event(
            "req-4", "execution.chat.timeout", level="ERROR"
        )
        assert event.level == "ERROR"


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_http_in(self):
        event = emit_http_in("r1", "gpt-4", stream=True, message_count=5)
        assert event.type == "execution.chat.http_in"
        assert event.data["client_model"] == "gpt-4"
        assert event.data["stream"] is True

    def test_emit_request_received(self):
        event = emit_request_received("r1", header={"x-key": "val"}, body_json={})
        assert event.type == "execution.chat.request_received"
        assert "header" in event.data

    def test_emit_tool_call(self):
        event = emit_tool_call("r1", [{"id": "tc1", "name": "foo"}])
        assert event.type == "execution.chat.tool_call"
        assert len(event.data["tool_calls"]) == 1

    def test_emit_tool_result(self):
        event = emit_tool_result("r1", "tc1", "my_func", "result")
        assert event.type == "execution.chat.tool_result"
        assert event.data["tool_call_id"] == "tc1"
        assert event.data["name"] == "my_func"

    def test_emit_conversation(self):
        event = emit_conversation("r1", role="user", text="hello")
        assert event.type == "execution.chat.conversation"
        assert event.data["role"] == "user"
        assert event.data["text"] == "hello"

    def test_emit_route_not_found(self):
        event = emit_route_not_found("r1", "unknown-model")
        assert event.type == "execution.chat.route_not_found"
        assert event.level == "ERROR"

    def test_emit_missing_upstream(self):
        event = emit_missing_upstream("r1", "my-route")
        assert event.type == "execution.chat.missing_upstream"
        assert event.level == "ERROR"

    def test_emit_invalid_upstream(self):
        event = emit_invalid_upstream("r1", "my-route", "localhost:1234")
        assert event.type == "execution.chat.invalid_upstream"
        assert event.level == "ERROR"

    def test_emit_route_resolved(self):
        event = emit_route_resolved(
            "r1", "gpt-4", "default", "gpt-4", "gpt-4", "gpt-4",
            False, 4096, 2048, []
        )
        assert event.type == "execution.chat.route_resolved"
        assert event.data["resolved_route"] == "default"

    def test_emit_override(self):
        event = emit_override("r1", "temperature", 0.7, 0.9)
        assert event.type == "execution.chat.override"
        assert event.data["param"] == "temperature"

    def test_emit_repacked(self):
        event = emit_repacked("r1", True, False, "http://x/v1/chat/completions",
                              100, 2000)
        assert event.type == "execution.chat.repacked"
        assert event.data["did_summarize"] is True

    def test_emit_fallback_chain(self):
        event = emit_fallback_chain("r1", ["model-b"], "model-a")
        assert event.type == "execution.chat.fallback_chain"
        assert event.data["primary_model"] == "model-a"

    def test_emit_request_start(self):
        event = emit_request_start("r1", stream=False)
        assert event.type == "execution.chat.request_start"

    def test_emit_request_route(self):
        event = emit_request_route("r1", True, "default", filters=["timestamp"])
        assert event.type == "execution.chat.request_route"
        assert event.data["filters"] == ["timestamp"]

    def test_emit_upstream_error(self):
        event = emit_upstream_error("r1", 502, "http://x", "route", "m", "err")
        assert event.type == "execution.chat.upstream_error"
        assert event.level == "ERROR"

    def test_emit_fallback(self):
        event = emit_fallback("r1", "model-a", "model-b")
        assert event.type == "execution.chat.fallback"
        assert event.data["from_model"] == "model-a"
        assert event.data["to_model"] == "model-b"

    def test_emit_pipeline_error(self):
        event = emit_pipeline_error("r1", "boom")
        assert event.type == "execution.chat.pipeline_error"
        assert event.level == "ERROR"

    def test_emit_assistant(self):
        event = emit_assistant("r1", "hello world", 11)
        assert event.type == "execution.chat.assistant"
        assert event.data["content"] == "hello world"
        assert event.data["total_length"] == 11

    def test_emit_assistant_with_tool_calls(self):
        event = emit_assistant("r1", "", 0, tool_calls=["gen_text"])
        assert event.data["tool_calls"] == ["gen_text"]

    def test_emit_assistant_with_reasoning(self):
        event = emit_assistant("r1", "", 0, reasoning_content="thinking...",
                               reasoning_length=9)
        assert event.data["reasoning_content"] == "thinking..."

    def test_emit_http_out(self):
        event = emit_http_out("r1", 200)
        assert event.type == "execution.chat.http_out"
        assert event.data["status"] == 200

    def test_emit_timeout(self):
        event = emit_timeout("r1")
        assert event.type == "execution.chat.timeout"
        assert event.level == "ERROR"

    def test_emit_failed(self):
        event = emit_failed("r1", "err", "http://x", "route", "m", "tb")
        assert event.type == "execution.chat.failed"
        assert event.level == "ERROR"

    def test_emit_strip_image_retry(self):
        event = emit_strip_image_retry("r1", 1, 3)
        assert event.type == "execution.chat.strip_image_retry"

    def test_emit_strip_image_done(self):
        event = emit_strip_image_done("r1", 0)
        assert event.type == "execution.chat.strip_image_done"
        assert event.level == "WARN"

    def test_emit_strip_image_error(self):
        event = emit_strip_image_error("r1", 1, "timeout")
        assert event.type == "execution.chat.strip_image_error"
        assert event.level == "WARN"

    def test_emit_strip_image_failed(self):
        event = emit_strip_image_failed("r1", 1, 502, "bad")
        assert event.type == "execution.chat.strip_image_failed"
        assert event.level == "WARN"

    def test_emit_performance_metrics(self):
        event = emit_performance_metrics(
            "r1",
            {"completion_tps": 42.0, "elapsed_ms": 1000.0},
            model="qwen3.8-27b",
            route_name="chat/deep",
            completion_tokens_source="usage",
        )
        assert event.type == "execution.chat.performance_metrics"
        assert event.data["completion_tps"] == 42.0
        assert event.data["route_name"] == "chat/deep"

    def test_emit_derived_performance_metrics_uses_canonical_calculation(self):
        event = emit_derived_performance_metrics(
            "r1",
            elapsed_ms=2500,
            completion_tokens=80,
            ttft_ms=500,
            prompt_tokens=100,
            total_tokens=180,
            model="qwen3.8-27b",
            route_name="chat/deep",
            completion_tokens_source="usage",
        )
        assert event.data["completion_tps"] == 40.0
        assert event.data["prompt_tps"] == 200.0


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_execution_events_dispatched(self):
        """All convenience wrappers emit through a subscribed dispatcher."""
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        # Call every wrapper, passing the dispatcher
        emit_http_in("r1", "m", dispatcher=dispatcher)
        emit_request_received("r1", {}, {}, dispatcher=dispatcher)
        emit_tool_call("r1", [], dispatcher=dispatcher)
        emit_tool_result("r1", "id", "fn", "c", dispatcher=dispatcher)
        emit_conversation("r1", "user", "t", dispatcher=dispatcher)
        emit_route_not_found("r1", "m", dispatcher=dispatcher)
        emit_missing_upstream("r1", "r", dispatcher=dispatcher)
        emit_invalid_upstream("r1", "r", "u", dispatcher=dispatcher)
        emit_route_resolved("r1", "m", "r", "m", "m", "m", False, 0, 0, [], dispatcher=dispatcher)
        emit_override("r1", "k", "old", "new", dispatcher=dispatcher)
        emit_repacked("r1", False, False, "u", 0, None, dispatcher=dispatcher)
        emit_fallback_chain("r1", [], "m", dispatcher=dispatcher)
        emit_request_start("r1", dispatcher=dispatcher)
        emit_request_route("r1", False, "r", [], dispatcher=dispatcher)
        emit_upstream_error("r1", 500, "u", "r", "m", "e", dispatcher=dispatcher)
        emit_fallback("r1", "a", "b", dispatcher=dispatcher)
        emit_pipeline_error("r1", "e", dispatcher=dispatcher)
        emit_assistant("r1", "", 0, dispatcher=dispatcher)
        emit_http_out("r1", 200, dispatcher=dispatcher)
        emit_timeout("r1", dispatcher=dispatcher)
        emit_failed("r1", "e", "u", "r", "m", "tb", dispatcher=dispatcher)
        emit_strip_image_retry("r1", 0, 0, dispatcher=dispatcher)
        emit_strip_image_done("r1", 0, dispatcher=dispatcher)
        emit_strip_image_error("r1", 0, "e", dispatcher=dispatcher)
        emit_strip_image_failed("r1", 0, 500, "b", dispatcher=dispatcher)
        emit_performance_metrics(
            "r1", {}, "m", "route", "usage", dispatcher=dispatcher
        )

        # All should have been captured by the dispatcher
        assert len(received) == 26
        types = {e.type for e in received}
        # Verify all expected event types are present
        expected_types = {
            "execution.chat.http_in",
            "execution.chat.request_received",
            "execution.chat.tool_call",
            "execution.chat.tool_result",
            "execution.chat.conversation",
            "execution.chat.route_not_found",
            "execution.chat.missing_upstream",
            "execution.chat.invalid_upstream",
            "execution.chat.route_resolved",
            "execution.chat.override",
            "execution.chat.repacked",
            "execution.chat.fallback_chain",
            "execution.chat.request_start",
            "execution.chat.request_route",
            "execution.chat.upstream_error",
            "execution.chat.fallback",
            "execution.chat.pipeline_error",
            "execution.chat.assistant",
            "execution.chat.http_out",
            "execution.chat.timeout",
            "execution.chat.failed",
            "execution.chat.strip_image_retry",
            "execution.chat.strip_image_done",
            "execution.chat.strip_image_error",
            "execution.chat.strip_image_failed",
            "execution.chat.performance_metrics",
        }
        assert types == expected_types
