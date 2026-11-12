"""Compact default-level transitions, with verbose diagnostics kept separate."""

import asyncio

import pytest

from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events_execution import emit_balance_decision, emit_execution_event
from keeprollming.observability.events_request import emit_admission_acquired, emit_admission_queued
from keeprollming.observability.formatters import PlainTextFormatter
from keeprollming.observability.projectors import Projector


class MemorySink:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text)


def test_basic_shows_queue_enter_exit_and_promotion_without_raw_estimator_dump():
    sink = MemorySink()
    dispatcher = EventDispatcher()
    projector = Projector("flow", level="BASIC", formatter=PlainTextFormatter(), sinks=[sink])
    projector.activate(dispatcher)
    emit_admission_acquired("direct", route="backend", max_concurrent=1, waited=False, dispatcher=dispatcher)
    assert not sink.lines
    emit_admission_queued(
        "q", route="backend", service_route="code/main", max_concurrent=1, queue_timeout_ms=1000, dispatcher=dispatcher
    )
    emit_admission_acquired(
        "q",
        route="backend",
        service_route="code/main",
        max_concurrent=1,
        waited=True,
        queue_wait_ms=250,
        routing_priority=10,
        effective_routing_priority=12,
        dispatcher=dispatcher,
    )
    emit_balance_decision(
        "q",
        requested_route="code/main",
        selected_route="fast",
        previous_route="slow",
        reroute_chain=["code/main", "fast"],
        alternatives=["fast", "slow"],
        affinity_state="hit",
        selection_reason="promotion_beneficial",
        selection_waited=True,
        selection_wait_ms=250,
        promotion_estimate={
            "decision": "promote",
            "estimated_net_savings_seconds": 15,
            "current_evidence": {"source": "recent"},
            "candidate_evidence": {"source": "historical"},
            "private_calculations": "DO_NOT_DUMP",
        },
        dispatcher=dispatcher,
    )
    assert len(sink.lines) == 3
    assert "admission.queued" in sink.lines[0] and "code/main" in sink.lines[0]
    assert "queue_wait_ms=250" in sink.lines[1] and "effective_routing_priority=12" in sink.lines[1]
    assert "promotion=promote" in sink.lines[2] and "from=slow" in sink.lines[2] and "gain_s=15" in sink.lines[2]
    assert "evidence=recent/historical" in sink.lines[2] and "DO_NOT_DUMP" not in sink.lines[2]
    assert all("\n" not in line.rstrip("\n") for line in sink.lines)


def test_balance_waiting_is_one_bounded_line():
    events = []
    dispatcher = EventDispatcher()
    dispatcher.subscribe("execution.routing", events.append)
    emit_execution_event(
        "q",
        "execution.routing.balance_waiting",
        route="code/main",
        wait_budget_ms=5000,
        candidate_states={f"backend-{i}": "capacity_unavailable" for i in range(20)},
        dispatcher=dispatcher,
    )
    text = PlainTextFormatter().format(events[0])
    assert "budget_ms=5000" in text and "+14 more" in text
    assert "backend-19" not in text and len(text) < 500


@pytest.mark.parametrize("headers_started", [False, True])
def test_preheader_cancel_is_basic_and_does_not_duplicate_stream_cancellation(monkeypatch, headers_started):
    from keeprollming import app

    async def run():
        dispatcher = EventDispatcher()
        events = []
        dispatcher.subscribe("request.lifecycle", events.append)
        monkeypatch.setattr(app, "get_event_dispatcher", lambda: dispatcher)

        async def cancelled(scope, receive, send):
            if headers_started:
                await send({"type": "http.response.start", "status": 200, "headers": []})
            raise asyncio.CancelledError

        async def noop(*_args):
            pass

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
            "state": {"req_id": "queued"},
        }
        with pytest.raises(asyncio.CancelledError):
            await app.RequestLifecycleMiddleware(cancelled)(scope, noop, noop)
        cancellations = [e for e in events if e.type == "request.lifecycle.cancelled"]
        assert len(cancellations) == (0 if headers_started else 1)
        if cancellations:
            assert cancellations[0].level == "BASIC" and cancellations[0].req_id == "queued"

    asyncio.run(run())
