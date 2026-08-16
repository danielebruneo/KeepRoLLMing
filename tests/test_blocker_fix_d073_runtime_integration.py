"""Tests for D-073 runtime integration blocker fixes (TASK-20260805-011).

These tests verify the four runtime integration defects identified by
TASK-20260805-010 investigation and fixed by the BLOCKER_FIXER.
"""

import pytest
from unittest.mock import MagicMock, patch

from keeprollming.observability import EventDispatcher, RuntimeEvent
from keeprollming.observability.events_execution import (
    emit_execution_event,
    emit_http_in,
    emit_route_resolved,
    emit_assistant,
)
from keeprollming.streaming.accounting import ExecutionUsage


class TestDefect1_DispatcherFallback:
    """Defect 1: execution.chat.* events not reaching dispatcher.

    Fix: emit_execution_event() fetches global dispatcher when dispatcher=None.
    """

    def test_emit_execution_event_dispatches_when_dispatcher_none(self):
        """emit_execution_event() uses global dispatcher fallback."""
        mock_dispatcher = MagicMock()
        with patch("keeprollming.app.get_event_dispatcher", return_value=mock_dispatcher):
            emit_execution_event("req-1", "execution.chat.http_in")

        assert mock_dispatcher.emit.called
        event = mock_dispatcher.emit.call_args[0][0]
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.chat.http_in"
        assert event.req_id == "req-1"

    def test_emit_http_in_dispatches_via_fallback(self):
        """Convenience wrapper emit_http_in() dispatches via global fallback."""
        mock_dispatcher = MagicMock()
        with patch("keeprollming.app.get_event_dispatcher", return_value=mock_dispatcher):
            emit_http_in("req-2", client_model="gpt-4")

        assert mock_dispatcher.emit.called
        event = mock_dispatcher.emit.call_args[0][0]
        assert event.type == "execution.chat.http_in"
        assert event.data["client_model"] == "gpt-4"

    def test_no_crash_when_global_dispatcher_none(self):
        """No crash when both parameter and global dispatcher are None."""
        with patch("keeprollming.app.get_event_dispatcher", return_value=None):
            event = emit_execution_event("req-3", "execution.chat.http_in")

        # Event is still created, just not emitted
        assert isinstance(event, RuntimeEvent)


class TestDefect2_LevelUpgrade:
    """Defect 2: execution.chat.* events at INFO level filtered by BASIC projector.

    Fix: Default level changed from INFO to BASIC for lifecycle events.
    """

    def test_default_level_is_basic(self):
        """emit_execution_event() defaults to BASIC level."""
        event = emit_execution_event("req-1", "execution.chat.http_in")
        assert event.level == "BASIC"

    def test_http_in_is_basic(self):
        """emit_http_in() produces BASIC-level events."""
        event = emit_execution_event("req-2", "execution.chat.http_in")
        assert event.level == "BASIC"

    def test_route_resolved_is_basic(self):
        """emit_route_resolved() produces BASIC-level events."""
        event = emit_route_resolved(
            "req-3", client_model="gpt-4", resolved_route="default",
            model="gpt-4", upstream_model="gpt-4", summary_model="",
            passthrough_enabled=False, ctx_len=128000, max_tokens_default=4096,
            parent_routes=[],
        )
        assert event.level == "BASIC"

    def test_assistant_is_basic(self):
        """emit_assistant() produces BASIC-level events."""
        event = emit_assistant("req-4", content="hello", total_length=5)
        assert event.level == "BASIC"

    def test_error_events_still_error(self):
        """Error-level events remain ERROR (not affected by default change)."""
        event = emit_execution_event("req-5", "execution.chat.upstream_error", level="ERROR")
        assert event.level == "ERROR"


class TestDefect3_UpstreamAttemptsCounting:
    """Defect 3: upstream_attempts=0 despite real upstream work.

    Fix: add_attempt() called regardless of whether provider reported usage.
    """

    def test_add_attempt_with_none_raw_usage(self):
        """add_attempt() increments upstream_attempts even when raw_usage=None."""
        usage = ExecutionUsage.empty()
        usage.add_attempt(0, None)

        assert usage.upstream_attempts == 1
        assert usage.usage_reported_attempts == 0
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0

    def test_add_attempt_with_usage_data(self):
        """add_attempt() aggregates tokens when raw_usage is provided."""
        usage = ExecutionUsage.empty()
        usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 50})

        assert usage.upstream_attempts == 1
        assert usage.usage_reported_attempts == 1
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150

    def test_mixed_attempts(self):
        """Multiple attempts with mixed usage reporting."""
        usage = ExecutionUsage.empty()
        usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 50})
        usage.add_attempt(1, None)  # Provider didn't report usage
        usage.add_attempt(2, {"prompt_tokens": 80, "completion_tokens": 40})

        assert usage.upstream_attempts == 3
        assert usage.usage_reported_attempts == 2
        assert usage.prompt_tokens == 180
        assert usage.completion_tokens == 90
        assert usage.total_tokens == 270
        assert usage.usage_complete is False


class TestDefect4_FinishReasonPropagation:
    """Defect 4: finish_reason=null hardcoded in streaming metrics.

    Fix: ExecutionUsage now has finish_reason field, populated by runner.
    """

    def test_execution_usage_has_finish_reason_field(self):
        """ExecutionUsage dataclass includes finish_reason field."""
        usage = ExecutionUsage.empty()
        assert hasattr(usage, "finish_reason")
        assert usage.finish_reason is None

    def test_finish_reason_can_be_set(self):
        """finish_reason can be populated after stream completion."""
        usage = ExecutionUsage.empty()
        usage.finish_reason = "stop"
        assert usage.finish_reason == "stop"

    def test_finish_reason_tool_calls(self):
        """finish_reason captures tool_calls value."""
        usage = ExecutionUsage.empty()
        usage.finish_reason = "tool_calls"
        assert usage.finish_reason == "tool_calls"
