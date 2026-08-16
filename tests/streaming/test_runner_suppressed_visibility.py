"""Test for suppressed events still being visible to later finalizers.

This test documents the critical D0 semantics:
- When a finalizer returns [], the event is suppressed for immediate output
- BUT later finalizers still observe the event
- This is required for buffering finalizers (like Timestamp) that need
  to coexist with semantic finalizers (like Nudge)
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from keeprollming.streaming.events import AssistantTextDelta, Done, Finish, StreamEvent
from keeprollming.streaming.finalizers import StreamFinalizer
from keeprollming.streaming.parser import StreamParser
from keeprollming.streaming.serializer import OpenAISSESerializer
from keeprollming.streaming.runner import run_stream
from tests.helpers.stream_client import (
    TestAssistantTextDelta,
    TestDone,
    TestFinish,
    parse_sse_events,
)


def _make_chunks(*frames) -> list:
    """Build a list of raw SSE chunks from frame strings or bytes."""
    result = []
    for f in frames:
        if isinstance(f, bytes):
            result.append(f)
        else:
            result.append(f.encode("utf-8"))
    return result


def _make_text_chunk(text: str) -> bytes:
    """Build a single SSE chunk with assistant text."""
    import json
    payload = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {payload}\n\n".encode("utf-8")


def _make_finish_chunk(reason: str = "stop") -> bytes:
    """Build a single SSE chunk with finish_reason."""
    import json
    payload = json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]})
    return f"data: {payload}\n\n".encode("utf-8")


def _collect_chunks(async_gen) -> list:
    """Consume an async iterator and collect all chunks."""
    result = []

    async def _collect():
        try:
            while True:
                chunk = await async_gen.__anext__()
                result.append(chunk)
        except StopAsyncIteration:
            pass

    asyncio.run(_collect())
    return result


def test_runner_chaining_suppressed_event_still_visible_to_later_finalizer():
    """Prove that suppressed events remain visible to later finalizers.

    Scenario:
    1. AssistantTextDelta("hello") arrives
    2. FakeSuppressingFinalizer (priority 20) returns [] (suppresses for output)
    3. FakeObserverFinalizer (priority 30) records that it saw "hello" and
       also returns [] (suppresses for output)
    4. Finish arrives
    5. Expected: "hello" is NOT in the output (suppressed), but both finalizers
       observed it during processing.
    """
    class FakeSuppressingFinalizer(StreamFinalizer):
        """Finalizer that suppresses AssistantTextDelta for output."""
        priority: int = 20

        def process_event(self, event: StreamEvent) -> list:
            if isinstance(event, AssistantTextDelta):
                return []  # Suppress for output
            return [event]

        def finalize(self) -> list:
            return []

    class FakeObserverFinalizer(StreamFinalizer):
        """Finalizer that observes events and records them."""
        priority: int = 30

        def __init__(self):
            super().__init__()
            self.observed_events: List[StreamEvent] = []

        def process_event(self, event: StreamEvent) -> list:
            if isinstance(event, AssistantTextDelta):
                self.observed_events.append(event)
                return []  # Also suppress for output
            return [event]

        def finalize(self) -> list:
            return []

    suppressing_fin = FakeSuppressingFinalizer()
    observer_fin = FakeObserverFinalizer()

    chunks = _make_chunks(
        _make_text_chunk("hello"),
        _make_finish_chunk("stop"),
        b"data: [DONE]\n\n",
    )

    async def _run():
        async for chunk in run_stream(
            upstream_chunks=iter(chunks),
            finalizers=[suppressing_fin, observer_fin],
            serializer=OpenAISSESerializer(),
            parser=StreamParser(),
        ):
            yield chunk

    frames = _collect_chunks(_run())
    events = parse_sse_events(frames)

    # Verify "hello" is NOT in the output (suppressed)
    hello_events = [
        e for e in events
        if isinstance(e, TestAssistantTextDelta) and e.delta == "hello"
    ]
    assert len(hello_events) == 0, (
        f"Suppressed event must NOT be in output, but found {len(hello_events)}."
    )

    # Verify both finalizers observed the event
    assert len(observer_fin.observed_events) == 1, (
        f"Observer finalizer must have observed the event, "
        f"but observed {len(observer_fin.observed_events)} events."
    )
    assert observer_fin.observed_events[0].delta == "hello", (
        f"Observer must have observed 'hello', "
        f"but observed '{observer_fin.observed_events[0].delta}'."
    )

    # Verify Finish and Done are present
    finish_events = [e for e in events if isinstance(e, TestFinish)]
    assert len(finish_events) == 1, "Expected exactly one Finish event"

    assert isinstance(events[-1], TestDone), "Done must be the last event"
