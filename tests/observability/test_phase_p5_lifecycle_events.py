"""Phase P5 — Legacy Caller Migration: lifecycle event emission tests.

Verifies that execution.chat.* and execution.streaming.* events flow through
the configured dispatcher/projector pipeline after Phase P5 migration.

TASK_ID: TASK-20260805-013
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


class TestEmitExecutionEventFallback:
    """Test that emit_execution_event() fallback fetches global dispatcher."""

    def test_emit_execution_event_fallback_fetches_global_dispatcher(self):
        """emit_execution_event() with dispatcher=None fetches global dispatcher.

        Verifies TASK-20260805-011 fix: when dispatcher is None, emit_execution_event()
        calls get_event_dispatcher() to fetch the global dispatcher.
        """
        from keeprollming.observability.events_execution import emit_execution_event

        mock_dispatcher = MagicMock()
        emitted_events = []
        mock_dispatcher.emit.side_effect = lambda event: emitted_events.append(event)

        # Patch at the app module level where get_event_dispatcher is defined
        with patch("keeprollming.app.get_event_dispatcher", return_value=mock_dispatcher):
            result = emit_execution_event(
                req_id="test-req-1",
                event_type="execution.chat.http_in",
                client_model="test-model",
                stream=False,
            )

            # Verify event was emitted through dispatcher
            assert len(emitted_events) == 1
            event = emitted_events[0]
            assert event.type == "execution.chat.http_in"
            assert event.req_id == "test-req-1"
            assert event.data["client_model"] == "test-model"

    def test_emit_execution_event_no_dispatcher_returns_event(self):
        """emit_execution_event() returns the event even when no dispatcher is available."""
        from keeprollming.observability.events_execution import emit_execution_event

        # Patch at the app module level to return None
        with patch("keeprollming.app.get_event_dispatcher", return_value=None):
            result = emit_execution_event(
                req_id="test-req-2",
                event_type="execution.chat.http_in",
                client_model="test-model",
            )

            # Event is created and returned (but not emitted)
            assert result is not None
            assert result.type == "execution.chat.http_in"
            assert result.req_id == "test-req-2"


class TestEmitStreamingEventFallback:
    """Test that emit_streaming_event() fallback to log() works."""

    def test_emit_streaming_event_with_dispatcher(self):
        """emit_streaming_event() with dispatcher emits through it."""
        from keeprollming.observability.events_streaming import emit_streaming_event

        mock_dispatcher = MagicMock()
        emitted_events = []
        mock_dispatcher.emit.side_effect = lambda event: emitted_events.append(event)

        result = emit_streaming_event(
            req_id="test-req-3",
            event_type="execution.streaming.handler_entry",
            dispatcher=mock_dispatcher,
        )

        assert len(emitted_events) == 1
        event = emitted_events[0]
        assert event.type == "execution.streaming.handler_entry"
        assert event.req_id == "test-req-3"

    def test_emit_streaming_event_no_dispatcher_is_noop(self):
        """emit_streaming_event() with dispatcher=None is a no-op (FIX-D072).

        The fallback to log() was removed because it bypassed the Projector
        architecture, printing JSON directly to stdout outside level filtering.
        When dispatcher is None, the call should do nothing.
        """
        from keeprollming.observability.events_streaming import emit_streaming_event

        # Patch at the logger module level to verify log() is NOT called
        with patch("keeprollming.logger.log") as mock_log:
            result = emit_streaming_event(
                req_id="test-req-4",
                event_type="execution.streaming.handler_entry",
                dispatcher=None,
            )

            # FIX-D072: No fallback to log() — call is a no-op
            mock_log.assert_not_called()
            # Event object is still created and returned
            assert result is not None
            assert result.type == "execution.streaming.handler_entry"


class TestChatCompletionsDispatcherWiring:
    """Test that chat_completions.py wires dispatcher through streaming handler."""

    def test_chat_completions_code_passes_dispatcher(self):
        """process_chat_request() passes dispatcher to process_streaming_request().

        Verifies Phase P5 change by inspecting the source code: chat_completions.py
        now fetches get_event_dispatcher() and passes it as a keyword argument to
        process_streaming_request().
        """
        import inspect
        from keeprollming.endpoints import chat_completions

        source = inspect.getsource(chat_completions.process_chat_request)

        # Verify the Phase P5 change: dispatcher is fetched and passed
        assert "get_event_dispatcher" in source, (
            "process_chat_request() does not call get_event_dispatcher()"
        )
        assert "dispatcher=dispatcher" in source or "dispatcher = get_event_dispatcher()" in source, (
            "process_chat_request() does not pass dispatcher to process_streaming_request()"
        )

    def test_streaming_handler_accepts_dispatcher_parameter(self):
        """process_streaming_request() accepts dispatcher parameter.

        Verifies that the streaming handler function signature includes dispatcher.
        """
        import inspect
        from keeprollming.endpoints.streaming_handlers import process_streaming_request

        sig = inspect.signature(process_streaming_request)
        params = list(sig.parameters.keys())

        assert "dispatcher" in params, (
            f"process_streaming_request() does not accept dispatcher parameter. "
            f"Parameters: {params}"
        )


class TestBuildPipelineDispatcherWiring:
    """Test that the mandatory V2 pipeline builder passes the dispatcher."""

    def test_build_pipeline_accepts_dispatcher_parameter(self):
        """_build_pipeline() accepts dispatcher parameter.

        Verifies Phase P5 change: streaming_handlers.py::_build_pipeline()
        now accepts dispatcher parameter and passes it to emit_pipeline_build().
        """
        from keeprollming.endpoints.streaming_handlers import _build_pipeline

        mock_dispatcher = MagicMock()

        # Create a mock route with filters
        mock_route = MagicMock()
        mock_route.filters = {"test_filter": {}}
        mock_route.name = "test-route"
        mock_route.api_key = None

        with patch(
            "keeprollming.endpoints.streaming_handlers.Pipeline.from_route_config"
        ) as mock_from_config:
            mock_pipeline = MagicMock()
            mock_from_config.return_value = mock_pipeline

            with patch(
                "keeprollming.endpoints.streaming_handlers.emit_pipeline_build"
            ) as mock_emit:
                result = _build_pipeline(
                    mock_route, req_id="test-req", dispatcher=mock_dispatcher
                )

                # Verify emit_pipeline_build was called with dispatcher
                mock_emit.assert_called_once()
                call_kwargs = mock_emit.call_args[1]
                assert call_kwargs["dispatcher"] is mock_dispatcher, (
                    "emit_pipeline_build() was not called with dispatcher parameter"
                )

    def test_build_pipeline_defaults_dispatcher_to_none(self):
        """_build_pipeline() defaults dispatcher to None."""
        from keeprollming.endpoints.streaming_handlers import _build_pipeline

        mock_route = MagicMock()
        mock_route.filters = {"test_filter": {}}
        mock_route.name = "test-route"
        mock_route.api_key = None

        with patch(
            "keeprollming.endpoints.streaming_handlers.Pipeline.from_route_config"
        ) as mock_from_config:
            mock_pipeline = MagicMock()
            mock_from_config.return_value = mock_pipeline

            with patch(
                "keeprollming.endpoints.streaming_handlers.emit_pipeline_build"
            ) as mock_emit:
                result = _build_pipeline(mock_route, req_id="test-req")

                call_kwargs = mock_emit.call_args[1]
                assert call_kwargs["dispatcher"] is None


class TestDiagnosticEmitUnconditional:
    """Diagnostic emissions bypass formatter-specific registration."""

    def test_log_emits_via_dispatcher_before_should_log_gating(self):
        """log() emits a diagnostic RuntimeEvent without a formatter whitelist.

        Verifies I-D072-01/I-D072-02: no RuntimeEvent is dropped because its name
        is unregistered in a formatter-specific whitelist.
        """
        from keeprollming.logger import log

        emitted_events = []
        mock_dispatcher = MagicMock()
        mock_dispatcher.emit.side_effect = lambda event: emitted_events.append(event)

        # Patch at the app module level where get_event_dispatcher is defined
        with patch("keeprollming.app.get_event_dispatcher", return_value=mock_dispatcher):
            # Call log(); a diagnostic event is emitted before projectors apply
            # their normal selector/level policy.
            log("INFO", "test_event_for_unconditional_emit", req_id="test-req", some_field="value")

            # Verify a diagnostic RuntimeEvent reached the dispatcher.
            assert len(emitted_events) > 0, (
                "log() did not emit via EventDispatcher. "
                "This violates I-D072-01/I-D072-02: events should be emitted via "
                "EventDispatcher regardless of _should_log() whitelist."
            )

            event = emitted_events[0]
            assert event.type == "diagnostic.test_event_for_unconditional_emit"
            assert event.req_id == "test-req"
