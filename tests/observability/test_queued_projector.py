"""Regression coverage for non-blocking optional observability projections."""

from __future__ import annotations

import time

import pytest

from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.projectors import Projector, QueuedProjector, Sink


def _event(sequence: int) -> RuntimeEvent:
    return RuntimeEvent(
        type="execution.streaming.progress",
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="execution", component="streaming"),
        data={"sequence": sequence},
        req_id="queued-projector-test",
        level="BASIC",
    )


class _SlowOrderedSink(Sink):
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.values: list[int] = []

    def write(self, text: str) -> None:
        time.sleep(self.delay_s)
        self.values.append(int(text))


class _SequenceFormatter:
    def format(self, event: RuntimeEvent) -> str:
        return str(event.data["sequence"])


@pytest.mark.asyncio
async def test_slow_sink_does_not_delay_event_emission_and_keeps_order() -> None:
    """A slow optional sink cannot make stream-event publication burst."""
    sink = _SlowOrderedSink(delay_s=0.05)
    projector = Projector(
        name="slow",
        selector="execution.streaming.*",
        level="BASIC",
        formatter=_SequenceFormatter(),
        sinks=[sink],
    )
    queued = QueuedProjector(projector, max_queue_size=8)
    dispatcher = EventDispatcher()
    await queued.start(dispatcher)
    try:
        started = time.perf_counter()
        for sequence in range(3):
            dispatcher.emit(_event(sequence))
        publish_elapsed = time.perf_counter() - started

        # Three synchronous flushes would take about 150 ms. The request path
        # only puts events into the bounded queue.
        assert publish_elapsed < 0.03
        await queued.wait_idle()
        assert sink.values == [0, 1, 2]
        assert queued.dropped_events == 0
    finally:
        await queued.stop()


@pytest.mark.asyncio
async def test_queue_overflow_is_counted_and_emits_structured_telemetry() -> None:
    """Loss is bounded to optional output and observable to other consumers."""
    sink = _SlowOrderedSink(delay_s=0.1)
    projector = Projector(
        name="overflowing",
        selector="execution.streaming.*",
        level="BASIC",
        formatter=_SequenceFormatter(),
        sinks=[sink],
    )
    queued = QueuedProjector(projector, max_queue_size=1)
    dispatcher = EventDispatcher()
    telemetry: list[RuntimeEvent] = []
    dispatcher.subscribe("execution.observability", telemetry.append)
    await queued.start(dispatcher)
    try:
        dispatcher.emit(_event(1))
        dispatcher.emit(_event(2))
        dispatcher.emit(_event(3))

        assert queued.dropped_events == 2
        assert len(telemetry) == 1
        assert telemetry[0].type == "execution.observability.projection_overflow"
        assert telemetry[0].data["projector"] == "overflowing"
        assert telemetry[0].data["dropped_events"] == 1
    finally:
        await queued.stop()
