"""Tests for statistically meaningful upstream decode progress telemetry."""

from __future__ import annotations

import json

from keeprollming.endpoints import streaming_handlers


class _Dispatcher:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


def _content_chunk(content: str) -> bytes:
    return (
        "data: "
        + json.dumps({"choices": [{"delta": {"content": content}}]})
        + "\n\n"
    ).encode()


def _semantic_chunk(delta: dict) -> bytes:
    return ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode()


def test_progress_omits_tps_for_tiny_initial_decode_sample(monkeypatch):
    """A one-token, sub-100ms sample is retained for TTFT but not TPS."""
    timestamps = iter((0.001, 0.002))
    monkeypatch.setattr(streaming_handlers.time, "perf_counter", lambda: next(timestamps))
    progress = streaming_handlers._StreamProgress(started_at=0.0)
    dispatcher = _Dispatcher()

    progress.observe_upstream(_content_chunk("ok"))
    progress.emit_if_due("request-1", dispatcher=dispatcher, force=True)

    data = dispatcher.events[-1].data
    assert data["output_tokens_est"] == 1
    assert data["decode_tps_est"] is None
    assert data["ttft_ms"] == 1.0


def test_progress_reports_tps_after_representative_decode_window(monkeypatch):
    """TPS resumes once both token-count and timing thresholds are met."""
    timestamps = iter((0.0, 0.2))
    monkeypatch.setattr(streaming_handlers.time, "perf_counter", lambda: next(timestamps))
    progress = streaming_handlers._StreamProgress(started_at=0.0)
    dispatcher = _Dispatcher()

    progress.observe_upstream(_content_chunk("x" * 32))
    progress.emit_if_due("request-2", dispatcher=dispatcher, force=True)

    data = dispatcher.events[-1].data
    assert data["output_tokens_est"] == 8
    assert data["decode_tps_est"] == 40.0


def test_progress_counts_reasoning_and_tool_call_output(monkeypatch):
    """Decode telemetry is based on all model-generated semantic channels."""
    timestamps = iter((0.0, 0.2))
    monkeypatch.setattr(streaming_handlers.time, "perf_counter", lambda: next(timestamps))
    progress = streaming_handlers._StreamProgress(started_at=0.0)
    dispatcher = _Dispatcher()

    progress.observe_upstream(_semantic_chunk({"reasoning_content": "r" * 16}))
    progress.observe_upstream(_semantic_chunk({"tool_calls": [{
        "index": 0,
        "function": {"name": "date", "arguments": '{"tz":"UTC"}'},
    }]}))
    progress.emit_if_due("request-3", dispatcher=dispatcher, force=True)

    data = dispatcher.events[-1].data
    assert data["output_chars"] == 32
    assert data["output_tokens_est"] == 8
    assert data["ttft_ms"] == 0.0
    assert data["decode_tps_est"] == 40.0
