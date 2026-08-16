"""Integration tests for streaming pipeline instrumentation (Phase O2)."""

import asyncio
import time
from typing import List

import pytest

from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.consumers import LoggerConsumer
from keeprollming.streaming.parser import StreamParser
from keeprollming.streaming.events import AssistantTextDelta, Finish, Done
from keeprollming.streaming.serializer import OpenAISSESerializer


def _collect_events(dispatcher: EventDispatcher) -> List[RuntimeEvent]:
    """Collect all events emitted to a LoggerConsumer registered on the dispatcher."""
    consumer = LoggerConsumer(capture=True)
    dispatcher.subscribe("streaming", consumer)
    return consumer


class TestParserInstrumentation:
    """Test StreamParser observability instrumentation."""

    def test_parser_emits_events_with_dispatcher(self):
        """StreamParser emits RuntimeEvents when dispatcher is provided."""
        dispatcher = EventDispatcher()
        consumer = LoggerConsumer(capture=True)
        dispatcher.subscribe("streaming", consumer)

        parser = StreamParser(dispatcher=dispatcher)
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"content\": \"Hello\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"stop\"}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        events = parser.parse_sync(chunks)

        # Parser should have produced StreamEvents
        assert len(events) > 0

        # Dispatcher should have received RuntimeEvents
        captured = consumer.captured
        assert len(captured) > 0

        # All captured events should be streaming.parser.event type
        for evt in captured:
            assert evt.type == "streaming.parser.event"
            assert evt.source.domain == "streaming"
            assert evt.source.component == "parser"

    def test_parser_no_op_without_dispatcher(self):
        """StreamParser is no-op when dispatcher is None."""
        parser = StreamParser(dispatcher=None)
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"content\": \"Hello\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"stop\"}]}\n\n",
            b"data: [DONE]\n\n",
        ]
        events = parser.parse_sync(chunks)
        assert len(events) > 0


class TestSerializerInstrumentation:
    """Test OpenAISSESerializer observability instrumentation."""

    def test_serializer_emits_events_with_dispatcher(self):
        """OpenAISSESerializer emits RuntimeEvents when dispatcher is provided."""
        dispatcher = EventDispatcher()
        consumer = LoggerConsumer(capture=True)
        dispatcher.subscribe("streaming", consumer)

        serializer = OpenAISSESerializer(dispatcher=dispatcher)
        events = [
            AssistantTextDelta(delta="Hello"),
            Finish(reason="stop"),
            Done(),
        ]
        serialized = serializer.serialize_events(events)

        # Should produce serialized output
        assert len(serialized) == 3

        # Dispatcher should have received RuntimeEvents
        captured = consumer.captured
        assert len(captured) == 3

        # All should be streaming.serializer.serialize type
        for evt in captured:
            assert evt.type == "streaming.serializer.serialize"
            assert evt.source.domain == "streaming"
            assert evt.source.component == "serializer"

    def test_serializer_no_op_without_dispatcher(self):
        """OpenAISSESerializer is no-op when dispatcher is None."""
        serializer = OpenAISSESerializer(dispatcher=None)
        events = [
            AssistantTextDelta(delta="Hello"),
            Finish(reason="stop"),
        ]
        serialized = serializer.serialize_events(events)
        assert len(serialized) == 2


class TestEndToEndEventFlow:
    """Test end-to-end event flow from producer to consumer."""

    def test_parser_to_consumer_flow(self):
        """Events flow from parser through dispatcher to consumer."""
        dispatcher = EventDispatcher()
        consumer = LoggerConsumer(capture=True)
        dispatcher.subscribe("streaming", consumer)

        parser = StreamParser(dispatcher=dispatcher)
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"content\": \"test\"}}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"stop\"}]}\n\n",
        ]
        parser.parse_sync(chunks)

        captured = consumer.captured
        assert len(captured) > 0
        # Verify event structure
        for evt in captured:
            assert evt.type.startswith("streaming.")
            assert evt.source.domain == "streaming"
            assert isinstance(evt.data, dict)

    def test_multiple_consumers_isolation(self):
        """Multiple consumers receive events independently."""
        dispatcher = EventDispatcher()
        consumer1 = LoggerConsumer(capture=True)
        consumer2 = LoggerConsumer(capture=True)
        dispatcher.subscribe("streaming", consumer1)
        dispatcher.subscribe("streaming", consumer2)

        parser = StreamParser(dispatcher=dispatcher)
        chunks = [
            b"data: {\"choices\": [{\"delta\": {\"content\": \"x\"}}]}\n\n",
        ]
        parser.parse_sync(chunks)

        # Both consumers should have received events
        assert len(consumer1.captured) > 0
        assert len(consumer2.captured) > 0
        assert len(consumer1.captured) == len(consumer2.captured)
