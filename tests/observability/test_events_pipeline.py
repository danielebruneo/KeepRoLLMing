"""Tests for pipeline event emission helpers (O7 Phase 3 migration).

Verifies that ``events_pipeline`` creates correct RuntimeEvent envelopes
and emits them through the EventDispatcher when available.
"""

import pytest

from keeprollming.observability import EventDispatcher, EventSource, RuntimeEvent
from keeprollming.observability.events_pipeline import (
    emit_pipeline_event,
    emit_process_request_error,
    emit_filter_triggered,
    emit_process_response_error,
    emit_stream_first_chunk,
    emit_stream_chunk_progress,
    emit_stream_filter_error,
    emit_stream_retry,
    emit_stream_stop,
    emit_stream_exhausted,
    emit_buffer_orphaned,
    emit_stream_started,
    emit_stream_request_filters_done,
    emit_stream_finalizers_built,
    emit_stream_completed,
    emit_run_stream_entry,
    emit_phase1_done,
    emit_phase2_start,
    emit_phase2_end,
    emit_no_upstream,
    emit_phase3_retry_start,
    emit_phase3_retry_result,
    emit_phase3_retry_end,
    emit_phase4_yield_content,
    emit_phase4_skip_tc,
    emit_phase4_yield_tc,
    emit_phase4_error,
    emit_phase4_fr_stop,
)


class TestEmitPipelineEvent:
    """Test the core emit_pipeline_event helper."""

    def test_emits_without_dispatcher(self):
        event = emit_pipeline_event("req-1", "execution.pipeline.test")
        assert isinstance(event, RuntimeEvent)
        assert event.type == "execution.pipeline.test"
        assert event.source == EventSource(domain="execution", component="pipeline")

    def test_emits_to_dispatcher(self):
        dispatcher = EventDispatcher()
        emitted_events: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", emitted_events.append)
        event = emit_pipeline_event("req-2", "execution.pipeline.test", dispatcher=dispatcher)
        assert isinstance(event, RuntimeEvent)
        assert len(emitted_events) == 1

    def test_custom_level(self):
        event = emit_pipeline_event("req-3", "execution.pipeline.error", level="ERROR")
        assert event.level == "ERROR"


class TestConvenienceWrappers:
    """Test each convenience wrapper produces correct RuntimeEvent."""

    def test_emit_process_request_error(self):
        event = emit_process_request_error("r1", "SystemPromptFilter", "boom")
        assert event.type == "execution.pipeline.process_request_error"
        assert event.level == "ERROR"

    def test_emit_filter_triggered(self):
        event = emit_filter_triggered("r1", "nudge_retry", "ModelNudgeFilter", "retrying")
        assert event.type == "execution.pipeline.filter_triggered"
        assert event.data["action"] == "nudge_retry"

    def test_emit_process_response_error(self):
        event = emit_process_response_error("r1", "SummarizationFilter", "err")
        assert event.type == "execution.pipeline.process_response_error"

    def test_emit_stream_first_chunk(self):
        event = emit_stream_first_chunk("r1", 1024, "data: ...")
        assert event.type == "execution.pipeline.stream_first_chunk"

    def test_emit_stream_chunk_progress(self):
        event = emit_stream_chunk_progress("r1", 500)
        assert event.type == "execution.pipeline.stream_chunk_progress"

    def test_emit_stream_filter_error(self):
        event = emit_stream_filter_error("r1", "TLSFilter", "timeout")
        assert event.type == "execution.pipeline.stream_filter_error"

    def test_emit_stream_retry(self):
        event = emit_stream_retry("r1", "ModelNudgeFilter", 100)
        assert event.type == "execution.pipeline.stream_retry"

    def test_emit_stream_stop(self):
        event = emit_stream_stop("r1", "ToolLoopStopper", 50)
        assert event.type == "execution.pipeline.stream_stop"

    def test_emit_stream_exhausted(self):
        event = emit_stream_exhausted("r1", 1000)
        assert event.type == "execution.pipeline.stream_exhausted"

    def test_emit_buffer_orphaned(self):
        event = emit_buffer_orphaned("r1", "NudgeFilter", 5)
        assert event.type == "execution.pipeline.buffer_orphaned"

    def test_emit_stream_started(self):
        event = emit_stream_started("r1", has_upstream=True)
        assert event.type == "execution.pipeline.stream_started"

    def test_emit_stream_request_filters_done(self):
        event = emit_stream_request_filters_done("r1")
        assert event.type == "execution.pipeline.stream_request_filters_done"

    def test_emit_stream_finalizers_built(self):
        event = emit_stream_finalizers_built("r1", finalizer_count=7)
        assert event.type == "execution.pipeline.stream_finalizers_built"

    def test_emit_stream_completed(self):
        event = emit_stream_completed("r1", execution_usage=True)
        assert event.type == "execution.pipeline.stream_completed"

    def test_emit_run_stream_entry(self):
        event = emit_run_stream_entry("r1")
        assert event.type == "execution.pipeline.run_stream_entry"

    def test_emit_phase1_done(self):
        event = emit_phase1_done("r1")
        assert event.type == "execution.pipeline.phase1_done"

    def test_emit_phase2_start(self):
        event = emit_phase2_start("r1")
        assert event.type == "execution.pipeline.phase2_start"

    def test_emit_phase2_end(self):
        event = emit_phase2_end("r1", captured_chunks=42)
        assert event.type == "execution.pipeline.phase2_end"

    def test_emit_no_upstream(self):
        event = emit_no_upstream("r1")
        assert event.type == "execution.pipeline.no_upstream"
        assert event.level == "WARN"

    def test_emit_phase3_retry_start(self):
        event = emit_phase3_retry_start("r1", max_retries=3)
        assert event.type == "execution.pipeline.phase3_retry_start"

    def test_emit_phase3_retry_result(self):
        event = emit_phase3_retry_result("r1", retry_count=0)
        assert event.type == "execution.pipeline.phase3_retry_result"

    def test_emit_phase3_retry_end(self):
        event = emit_phase3_retry_end("r1")
        assert event.type == "execution.pipeline.phase3_retry_end"

    def test_emit_phase4_yield_content(self):
        event = emit_phase4_yield_content("r1", chunk_size=100)
        assert event.type == "execution.pipeline.phase4_yield_content"

    def test_emit_phase4_skip_tc(self):
        event = emit_phase4_skip_tc("r1")
        assert event.type == "execution.pipeline.phase4_skip_tc"

    def test_emit_phase4_yield_tc(self):
        event = emit_phase4_yield_tc("r1", tool_call_index=2)
        assert event.type == "execution.pipeline.phase4_yield_tc"

    def test_emit_phase4_error(self):
        event = emit_phase4_error("r1", error="boom")
        assert event.type == "execution.pipeline.phase4_error"
        assert event.level == "ERROR"

    def test_emit_phase4_fr_stop(self):
        event = emit_phase4_fr_stop("r1")
        assert event.type == "execution.pipeline.phase4_fr_stop"


class TestDispatcherIntegration:
    """Test that events flow through the dispatcher correctly."""

    def test_all_pipeline_events_dispatched(self):
        dispatcher = EventDispatcher()
        received: list[RuntimeEvent] = []
        dispatcher.subscribe("execution", received.append)

        emit_process_request_error("r1", "F", "e", dispatcher=dispatcher)
        emit_filter_triggered("r1", "a", "F", dispatcher=dispatcher)
        emit_process_response_error("r1", "F", "e", dispatcher=dispatcher)
        emit_stream_first_chunk("r1", 100, dispatcher=dispatcher)
        emit_stream_chunk_progress("r1", 500, dispatcher=dispatcher)
        emit_stream_filter_error("r1", "F", "e", dispatcher=dispatcher)
        emit_stream_retry("r1", "F", 10, dispatcher=dispatcher)
        emit_stream_stop("r1", "F", 10, dispatcher=dispatcher)
        emit_stream_exhausted("r1", 100, dispatcher=dispatcher)
        emit_buffer_orphaned("r1", "F", 5, dispatcher=dispatcher)
        emit_stream_started("r1", dispatcher=dispatcher)
        emit_stream_request_filters_done("r1", dispatcher=dispatcher)
        emit_stream_finalizers_built("r1", 3, dispatcher=dispatcher)
        emit_stream_completed("r1", dispatcher=dispatcher)
        emit_run_stream_entry("r1", dispatcher=dispatcher)
        emit_phase1_done("r1", dispatcher=dispatcher)
        emit_phase2_start("r1", dispatcher=dispatcher)
        emit_phase2_end("r1", 0, dispatcher=dispatcher)
        emit_no_upstream("r1", dispatcher=dispatcher)
        emit_phase3_retry_start("r1", 3, dispatcher=dispatcher)
        emit_phase3_retry_result("r1", 0, dispatcher=dispatcher)
        emit_phase3_retry_end("r1", dispatcher=dispatcher)
        emit_phase4_yield_content("r1", 10, dispatcher=dispatcher)
        emit_phase4_skip_tc("r1", dispatcher=dispatcher)
        emit_phase4_yield_tc("r1", 1, dispatcher=dispatcher)
        emit_phase4_error("r1", "e", dispatcher=dispatcher)
        emit_phase4_fr_stop("r1", dispatcher=dispatcher)

        assert len(received) == 27
