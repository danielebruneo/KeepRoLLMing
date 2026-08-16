"""Tests for PlainTextFormatter human-readable rendering (D-072 §8).

Verifies that the PLAIN formatter produces coherent, readable operational logs
covering all 11 categories defined in D-072 §8:
1. Request identity
2. Route/model/upstream
3. Material parameters
4. Content transcripts
5. Tool calls/results
6. Filter/pipeline transformations
7. Retries/nudges
8. Response state
9. Usage/tokens
10. Material performance
11. Errors
"""

import time

import pytest

from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.formatters import PlainTextFormatter
from keeprollming.observability.projectors import _colorize_stdout_plain


def _make_event(
    event_type: str,
    data: dict | None = None,
    req_id: str | None = "test-req",
    level: str = "INFO",
    domain: str = "test",
    component: str = "test",
) -> RuntimeEvent:
    """Helper to create a RuntimeEvent for testing."""
    return RuntimeEvent(
        type=event_type,
        timestamp_ns=time.time_ns(),
        source=EventSource(domain=domain, component=component),
        data=data or {},
        req_id=req_id,
        level=level,
    )


class TestPlainTextFormatterNotJsonDump:
    """Verify PLAIN output is NOT RuntimeEvent JSON dumps (D-072 §8)."""

    def test_output_not_json_object(self):
        """PLAIN output does not start with '{'."""
        formatter = PlainTextFormatter()
        event = _make_event("test.event", {"key": "value"})
        result = formatter.format(event)
        assert not result.strip().startswith("{")

    def test_output_no_json_envelope_fields(self):
        """PLAIN output does not contain JSON envelope structure."""
        formatter = PlainTextFormatter()
        event = _make_event("test.event", {"data": True})
        result = formatter.format(event)
        assert '"type":' not in result
        assert '"source":' not in result
        assert '"timestamp_ms":' not in result

    def test_output_is_structured_text(self):
        """PLAIN output is structured text with event type."""
        formatter = PlainTextFormatter()
        event = _make_event("request.lifecycle.received", {"model": "gpt-4"})
        result = formatter.format(event)
        assert "request.received" in result

    def test_stream_progress_marks_unrepresentative_tps_unavailable(self):
        formatter = PlainTextFormatter()
        result = formatter.format(_make_event(
            "execution.streaming.progress",
            {"output_tokens_est": 1, "decode_tps_est": None},
        ))
        assert "decode_tps_est=unavailable" in result


class TestPlainTextFormatterReqIdGrouping:
    """Verify req_id grouping indicator is present."""

    def test_req_id_shown_in_brackets(self):
        """req_id appears as bracketed tag for grouping."""
        formatter = PlainTextFormatter()
        event = _make_event("test.event", req_id="abc123")
        result = formatter.format(event)
        assert "[abc123]" in result

    def test_no_req_id_tag_when_none(self):
        """No bracketed req_id when None."""
        formatter = PlainTextFormatter()
        event = _make_event("test.event", req_id=None)
        result = formatter.format(event)
        assert "[None]" not in result


class TestRequestRouteRendering:
    """The active filter chain must be useful in BASIC PLAIN logs."""

    def test_renders_filter_names_and_empty_chain(self):
        formatter = PlainTextFormatter()
        active = formatter.format(_make_event(
            "execution.chat.request_route",
            {"stream": True, "route": "chat/main", "filters": ["model_nudge", "timestamp"]},
        ))
        empty = formatter.format(_make_event(
            "execution.chat.request_route",
            {"stream": True, "route": "chat/main", "filters": []},
        ))
        assert "filters=[model_nudge, timestamp]" in active
        assert "filters=[]" in empty


class TestStdoutPlainColors:
    """ANSI decoration is confined to the stdout projection."""

    def test_event_structure_is_colored_but_transcript_text_is_not(self, monkeypatch):
        import keeprollming.logging.constants as constants

        monkeypatch.setattr(constants, "LOG_PLAIN_COLORS", True)
        text = (
            "2026-08-10T19:42:45.155Z [37d81492] "
            "execution.chat.assistant length=5 finish_reason=stop\n"
            "    ASSISTANT: length=5\n"
            "        hello"
        )
        result = _colorize_stdout_plain(text)
        assert constants.ANSI_DIM in result
        assert constants.ANSI_CYAN in result
        assert constants.ANSI_BLUE in result
        assert constants.ANSI_YELLOW in result
        assert "        hello" in result

    def test_tool_stdout_and_stderr_have_distinct_payload_colors(self, monkeypatch):
        import keeprollming.logging.constants as constants

        monkeypatch.setattr(constants, "LOG_PLAIN_COLORS", True)
        result = _colorize_stdout_plain(
            "    TOOL_RESULT:\n"
            "        stdout:\n"
            "        normal output\n"
            "        stderr:\n"
            "        error output"
        )

        assert constants.ANSI_BRIGHT_YELLOW in result
        assert constants.ANSI_BRIGHT_RED in result
        assert "normal output" in result
        assert "error output" in result


class TestRequestIdentityRendering:
    """D-072 §8 category 1: Request identity (req_id, timestamp, method, path)."""

    def test_request_received_shows_model_and_streaming(self):
        """request.received includes model and streaming flag."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "request.lifecycle.received",
            {"client_model": "gpt-4", "stream": True, "endpoint": "/v1/chat/completions"},
            domain="request",
            component="lifecycle",
        )
        result = formatter.format(event)
        assert "request.received" in result
        assert "model=gpt-4" in result
        assert "streaming=true" in result

    def test_request_completed_shows_status_and_latency(self):
        """request.completed includes status and latency."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "request.lifecycle.completed",
            {"status": 200, "elapsed_ms": 150.5},
            domain="request",
            component="lifecycle",
        )
        result = formatter.format(event)
        assert "request.completed" in result
        assert "status=200" in result
        assert "latency_ms=" in result

    def test_request_failed_shows_error(self):
        """request.failed includes error details."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "request.lifecycle.failed",
            {"error": "upstream timeout", "status": 504},
            domain="request",
            component="lifecycle",
        )
        result = formatter.format(event)
        assert "request.failed" in result
        assert "upstream timeout" in result


class TestUsageRendering:
    """Terminal usage summaries must distinguish missing provider usage."""

    def test_missing_upstream_usage_is_not_rendered_as_zero_tokens(self):
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.performance.request_complete",
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "finish_reason": "stop",
                "elapsed_ms": 42.0,
                "upstream_attempts": 1,
                "usage_reported_attempts": 0,
                "usage_complete": False,
            },
            domain="execution",
            component="performance",
        )

        result = formatter.format(event)

        assert "prompt=? completion=? total=? usage=unavailable" in result
        assert "prompt=0" not in result
        assert "usage_reported_attempts=0" in result


class TestPerformanceMetricsRendering:
    """The terminal performance record has a readable PLAIN projection."""

    def test_performance_metrics_shows_dashboard_values(self):
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.performance_metrics",
            {
                "model": "qwen3.8-27b",
                "route_name": "chat/deep",
                "elapsed_ms": 2500.0,
                "ttft_ms": 500.0,
                "prompt_tokens": 100,
                "cached_prompt_tokens": 75,
                "uncached_prompt_tokens": 25,
                "completion_tokens": 80,
                "total_tokens": 180,
                "completion_tps": 40.0,
                "prompt_tps": 200.0,
                "total_tps": 72.0,
                "completion_tokens_source": "usage",
            },
            domain="execution",
            component="chat",
        )

        result = formatter.format(event)

        assert "execution.chat.performance_metrics" in result
        assert 'model="qwen3.8-27b"' in result
        assert 'route="chat/deep"' in result
        assert "tps=40.0" in result
        assert "prompt_tps=200.0" in result
        assert "cached_prompt=75" in result
        assert "uncached_prompt=25" in result


class TestRouteModelUpstreamRendering:
    """D-072 §8 category 2: Route/model/upstream resolution."""

    def test_routing_resolved_shows_route_and_model(self):
        """routing.resolved includes route name and model."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "routing.resolution.resolved",
            {
                "resolved_route": "default",
                "upstream_model": "gpt-4",
                "upstream_url": "https://api.openai.com/v1/chat/completions",
            },
            domain="routing",
            component="resolution",
        )
        result = formatter.format(event)
        assert "routing.resolved" in result
        assert 'route="default"' in result
        assert "model=gpt-4" in result

    def test_upstream_url_shortened(self):
        """Long upstream URLs are shortened for readability."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "routing.resolution.resolved",
            {"upstream_url": "https://api.openai.com/v1/chat/completions?param=value"},
            domain="routing",
            component="resolution",
        )
        result = formatter.format(event)
        assert "api.openai.com" in result


class TestMaterialParametersRendering:
    """D-072 §8 category 3: Material parameters (streaming, tools, pipeline)."""

    def test_http_in_shows_material_params(self):
        """execution.http_in includes streaming flag and message count."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.http_in",
            {"client_model": "gpt-4", "stream": True, "message_count": 5},
            domain="execution",
            component="chat",
        )
        result = formatter.format(event)
        assert "execution.http_in" in result
        assert "streaming=true" in result
        assert "messages=5" in result


class TestContentTranscriptRendering:
    """D-072 §8 category 4: Content transcripts (system/user/assistant messages)."""

    def test_conversation_short_is_semantic_transcript(self):
        """Conversation content is rendered as a readable role section."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.conversation",
            {"role": "user", "text": "What is the meaning of life?"},
            domain="execution",
            component="chat",
        )
        result = formatter.format(event)
        assert "execution.chat.conversation role=user length=28" in result
        assert "USER:" in result
        assert "USER:" in result
        assert "meaning of life" in result

    def test_conversation_long_indented(self):
        """Long conversation content uses indented blocks."""
        formatter = PlainTextFormatter()
        long_text = "This is a very long message that exceeds the threshold for inline display. " * 10
        event = _make_event(
            "execution.chat.conversation",
            {"role": "assistant", "text": long_text},
            domain="execution",
            component="chat",
        )
        result = formatter.format(event)
        lines = result.split("\n")
        assert len(lines) > 1
        assert "execution.chat.conversation" in lines[0]
        assert "ASSISTANT:" in lines[1]
        # Indented content lines
        assert lines[2].startswith("        ")

    def test_assistant_response_with_finish_reason(self):
        """Assistant response includes finish reason."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.assistant",
            {"content": "Hello!", "total_length": 6, "finish_reason": "stop"},
            domain="execution",
            component="chat",
        )
        result = formatter.format(event)
        assert "execution.chat.assistant length=6" in result
        assert "ASSISTANT:" in result
        assert "finish_reason=stop" in result

    def test_assistant_renders_reasoning_before_final_response(self):
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.assistant",
            {
                "content": "Final answer.",
                "total_length": 13,
                "reasoning_content": "First think.",
                "reasoning_length": 12,
            },
            domain="execution", component="chat",
        )
        result = formatter.format(event)
        assert "REASONING:" in result
        assert "ASSISTANT:" in result
        assert result.index("REASONING:") < result.index("ASSISTANT:")
        assert "        First think." in result
        assert "        Final answer." in result

    def test_tool_call_turn_renders_call_without_empty_assistant_block(self):
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.assistant",
            {
                "content": "",
                "total_length": 0,
                "finish_reason": "tool_calls",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {"name": "bash_tool", "arguments": '{"command":"date"}'},
                }],
            },
            domain="execution", component="chat",
        )
        result = formatter.format(event)
        assert "TOOL_CALL:" in result
        assert "name=bash_tool" in result
        assert '{"command":"date"}' in result
        assert "ASSISTANT:" not in result

    def test_transcript_wrap_preserves_eight_space_indent(self, monkeypatch):
        import keeprollming.logging.constants as constants

        monkeypatch.setattr(constants, "LOG_PLAIN_WRAP_WIDTH", 32)
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.conversation",
            {"role": "system", "text": "A system prompt with enough words to wrap cleanly."},
            domain="execution", component="chat",
        )
        result = formatter.format(event)
        body_lines = result.splitlines()[2:]
        assert len(body_lines) > 1
        assert all(line.startswith("        ") for line in body_lines)

    def test_override_names_original_and_replacement_values(self):
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.override",
            {"param": "max_tokens", "old_value": 8192, "new_value": 4096},
            domain="execution", component="chat",
        )
        result = formatter.format(event)
        assert "param=max_tokens" in result
        assert "original=8192" in result
        assert "value=4096" in result


class TestToolCallsResultsRendering:
    """D-072 §8 category 5: Tool calls/results."""

    def test_tool_call_shows_name_and_args(self):
        """Tool call event shows function name and arguments."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.tool_call",
            {
                "tool_calls": [
                    {"id": "tc1", "name": "get_weather", "arguments": '{"city":"Rome"}'}
                ]
            },
            domain="execution",
            component="chat",
        )
        result = formatter.format(event)
        assert "execution.chat.tool_call count=1" in result
        assert "TOOL_CALL:" in result
        assert "name=get_weather" in result

    def test_tool_result_shows_name_and_result(self):
        """Tool result event shows function name and result."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.tool_result",
            {"tool_call_id": "tc1", "name": "get_weather", "result": "25°C sunny"},
            domain="execution",
            component="chat",
        )
        result = formatter.format(event)
        assert "execution.chat.tool_result" in result
        assert "TOOL_RESULT:" in result
        assert "name=get_weather" in result
        assert "25°C sunny" in result


class TestFilterPipelineRendering:
    """D-072 §8 category 6: Filter/pipeline transformations."""

    def test_filter_triggered_shows_action(self):
        """Filter triggered event shows filter name and action."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.pipeline.filter_triggered",
            {"action": "nudge_retry", "filter": "ModelNudgeFilter"},
            domain="execution",
            component="pipeline",
        )
        result = formatter.format(event)
        assert "pipeline.filter_triggered" in result
        assert "filter=ModelNudgeFilter" in result

    def test_system_prompt_injection(self):
        """System prompt injection shown as filter action."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.pipeline.filter_triggered",
            {"action": "system_prompt_injected", "filter": "SystemPromptFilter"},
            domain="execution",
            component="pipeline",
        )
        result = formatter.format(event)
        assert "SystemPromptFilter" in result


class TestRetriesNudgesRendering:
    """D-072 §8 category 7: Retries/nudges."""

    def test_stream_retry_shows_filter_and_delay(self):
        """Stream retry event shows filter and delay."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.pipeline.stream_retry",
            {"filter": "ModelNudgeFilter", "delay_ms": 100},
            domain="execution",
            component="pipeline",
        )
        result = formatter.format(event)
        assert "pipeline.retry" in result
        assert "ModelNudgeFilter" in result
        assert "delay_ms=100" in result

    def test_fallback_shows_model_transition(self):
        """Fallback event shows model transition."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.fallback",
            {"from_model": "gpt-4", "to_model": "gpt-3.5-turbo"},
            domain="execution",
            component="chat",
        )
        result = formatter.format(event)
        assert "execution.fallback" in result
        assert "gpt-4" in result
        assert "gpt-3.5-turbo" in result


class TestUsageTokensRendering:
    """D-072 §8 category 9: Usage/tokens."""

    def test_usage_captured_shows_token_counts(self):
        """Usage captured event shows prompt/completion/total tokens."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.accounting.usage.captured",
            {"prompt_tokens": 150, "completion_tokens": 320, "total_tokens": 470},
            domain="execution",
            component="accounting",
        )
        result = formatter.format(event)
        assert "accounting.usage" in result
        assert "prompt=150" in result
        assert "completion=320" in result
        assert "total=470" in result

    def test_usage_finalized_shows_attempts_and_cost(self):
        """Usage finalized event shows total attempts and cost."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.accounting.usage.finalized",
            {"total_attempts": 3, "total_cost": 0.05},
            domain="execution",
            component="accounting",
        )
        result = formatter.format(event)
        assert "accounting.finalized" in result
        assert "attempts=3" in result


class TestPerformanceRendering:
    """D-072 §8 category 10: Material performance (latency, attempts)."""

    def test_upstream_response_shows_latency(self):
        """Upstream response event shows latency."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "upstream.response",
            {"status": 200, "latency_ms": 5370.2},
            domain="upstream",
            component="client",
        )
        result = formatter.format(event)
        assert "upstream.response" in result
        assert "latency_ms=" in result


class TestErrorRendering:
    """D-072 §8 category 11: Errors with details."""

    def test_execution_failed_shows_error_details(self):
        """Failed execution shows error message and context."""
        formatter = PlainTextFormatter()
        event = _make_event(
            "execution.chat.failed",
            {
                "error": "Connection refused",
                "status": 502,
                "route": "default",
                "model": "gpt-4",
            },
            domain="execution",
            component="chat",
            level="ERROR",
        )
        result = formatter.format(event)
        assert "execution.failed" in result
        assert "Connection refused" in result
        assert "status=502" in result

    def test_error_with_traceback_shows_stacked_lines(self):
        """Error with traceback shows indented stack lines."""
        formatter = PlainTextFormatter()
        tb = "Traceback (most recent call last):\n  File 'app.py', line 10\n    raise\nError: boom"
        event = _make_event(
            "execution.chat.failed",
            {"error": "boom", "traceback": tb},
            domain="execution",
            component="chat",
            level="ERROR",
        )
        result = formatter.format(event)
        lines = result.split("\n")
        assert len(lines) > 1
        # Traceback lines are indented
        assert any(l.startswith("    ") for l in lines[1:])


class TestTimestampFormat:
    """Verify timestamp format is ISO-like with milliseconds."""

    def test_timestamp_has_iso_format(self):
        """Timestamp uses ISO-like format with T separator and Z suffix."""
        formatter = PlainTextFormatter()
        event = _make_event("test.event")
        result = formatter.format(event)
        assert "T" in result
        assert "Z" in result

    def test_timestamp_has_milliseconds(self):
        """Timestamp includes millisecond precision."""
        formatter = PlainTextFormatter()
        event = _make_event("test.event")
        result = formatter.format(event)
        # Format: 2026-08-04T14:52:00.123Z
        parts = result.split(".")[1].split(" ")[0] if "." in result else ""
        assert len(parts) == 4  # "123Z"


class TestLargePayloadHandling:
    """Verify large payloads use indented blocks, not inline JSON."""

    def test_large_string_truncated_inline(self):
        """Very long strings are truncated in generic rendering."""
        formatter = PlainTextFormatter()
        long_val = "x" * 300
        event = _make_event("test.event", {"data": long_val})
        result = formatter.format(event)
        assert len(result) < 500  # Should be truncated

    def test_dict_list_shown_as_item_count(self):
        """Dict/list values shown as item count, not dumped."""
        formatter = PlainTextFormatter()
        event = _make_event("test.event", {"items": [1, 2, 3], "config": {"a": 1}})
        result = formatter.format(event)
        assert "[3 items]" in result
        assert "[1 items]" in result
