"""Unit tests for EventDispatcher (Phase O2)."""

import asyncio
import json
import time

import pytest

from keeprollming.observability.consumers import LoggerConsumer
from keeprollming.observability.dispatcher import EventDispatcher
from keeprollming.observability.events import EventSource, RuntimeEvent


class TestEventDispatcherSubscribe:
    """Test subscription mechanics."""

    def test_subscribe_sync(self):
        """subscribe() registers a sync consumer for a namespace prefix."""
        dispatcher = EventDispatcher()
        received = []

        def consumer(event):
            received.append(event)

        dispatcher.subscribe("streaming", consumer)
        assert "streaming" in dispatcher._consumers
        assert dispatcher._consumers["streaming"] == [consumer]

    def test_subscribe_async(self):
        """subscribe_async() registers an async consumer for a namespace prefix."""
        dispatcher = EventDispatcher()
        received = []

        async def consumer(event):
            received.append(event)

        dispatcher.subscribe_async("streaming", consumer)
        assert "streaming" in dispatcher._async_consumers

    def test_subscribe_multiple_consumers(self):
        """Multiple consumers can be registered for the same prefix."""
        dispatcher = EventDispatcher()
        results = []

        def c1(event):
            results.append("c1")

        def c2(event):
            results.append("c2")

        dispatcher.subscribe("streaming", c1)
        dispatcher.subscribe("streaming", c2)
        assert len(dispatcher._consumers["streaming"]) == 2


class TestEventDispatcherEmit:
    """Test emission mechanics."""

    def test_emit_calls_matching_consumers(self):
        """emit() calls consumers whose prefix matches the event type."""
        dispatcher = EventDispatcher()
        received = []

        def consumer(event):
            received.append(event)

        dispatcher.subscribe("streaming", consumer)
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )
        dispatcher.emit(event)
        assert len(received) == 1
        assert received[0] is event

    def test_emit_no_match(self):
        """emit() drops silently when no consumers match (INV-01)."""
        dispatcher = EventDispatcher()
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )
        # Should not raise
        dispatcher.emit(event)

    def test_emit_exact_prefix_match(self):
        """emit() matches exact prefix (not just substring)."""
        dispatcher = EventDispatcher()
        received = []

        def consumer(event):
            received.append(event)

        dispatcher.subscribe("streaming", consumer)
        # "streaming" should match "streaming.parser.event"
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )
        dispatcher.emit(event)
        assert len(received) == 1

    def test_emit_unknown_event_type(self):
        """Unknown event types are still published (INV-01)."""
        dispatcher = EventDispatcher()
        received = []

        def consumer(event):
            received.append(event)

        dispatcher.subscribe("unknown", consumer)
        event = RuntimeEvent(
            type="unknown.custom.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="unknown", component="custom"),
            data={"test": True},
            level="DEBUG",
        )
        dispatcher.emit(event)
        assert len(received) == 1

    def test_emit_failure_isolation(self):
        """One consumer failure does not block others (INV-10)."""
        dispatcher = EventDispatcher()
        results = []

        def good_consumer(event):
            results.append("good")

        def bad_consumer(event):
            raise ValueError("boom")

        dispatcher.subscribe("streaming", good_consumer)
        dispatcher.subscribe("streaming", bad_consumer)
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )
        # Should not raise
        dispatcher.emit(event)
        assert "good" in results

    def test_emit_no_consumers_registered(self):
        """When no consumers registered, emit drops silently (Post-handoff Clarification 1)."""
        dispatcher = EventDispatcher()
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )
        # Should not raise
        dispatcher.emit(event)


class TestEventDispatcherTraceAll:
    """Test TRACE_ALL behavior (INV-02)."""

    def test_trace_all_property(self):
        """trace_all property is readable and writable."""
        dispatcher = EventDispatcher()
        assert dispatcher.trace_all is False
        dispatcher.trace_all = True
        assert dispatcher.trace_all is True

    def test_trace_all_bypass_behavior(self):
        """trace_all=True causes emit to reach consumers whose prefix does NOT match."""
        dispatcher = EventDispatcher(trace_all=True)
        streaming_received = []
        filter_received = []

        def streaming_consumer(event):
            streaming_received.append(event)

        def filter_consumer(event):
            filter_received.append(event)

        dispatcher.subscribe("streaming", streaming_consumer)
        dispatcher.subscribe("filter", filter_consumer)

        # Emit a filter-scoped event
        event = RuntimeEvent(
            type="filter.chain.executed",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="filter", component="chain"),
            data={"test": True},
            level="DEBUG",
        )
        dispatcher.emit(event)

        # With trace_all=True, BOTH consumers receive the event
        # even though "streaming" prefix does not match "filter.chain.executed"
        assert len(streaming_received) == 1
        assert len(filter_received) == 1

    def test_trace_all_false_preserves_prefix_matching(self):
        """trace_all=False (default) uses normal prefix matching."""
        dispatcher = EventDispatcher(trace_all=False)
        streaming_received = []
        filter_received = []

        def streaming_consumer(event):
            streaming_received.append(event)

        def filter_consumer(event):
            filter_received.append(event)

        dispatcher.subscribe("streaming", streaming_consumer)
        dispatcher.subscribe("filter", filter_consumer)

        event = RuntimeEvent(
            type="filter.chain.executed",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="filter", component="chain"),
            data={"test": True},
            level="DEBUG",
        )
        dispatcher.emit(event)

        # Only filter consumer matches
        assert len(streaming_received) == 0
        assert len(filter_received) == 1

    def test_trace_all_toggle_at_runtime(self):
        """trace_all can be toggled at runtime and affects subsequent emits."""
        dispatcher = EventDispatcher(trace_all=False)
        streaming_received = []
        filter_received = []

        def streaming_consumer(event):
            streaming_received.append(event)

        def filter_consumer(event):
            filter_received.append(event)

        dispatcher.subscribe("streaming", streaming_consumer)
        dispatcher.subscribe("filter", filter_consumer)

        # Emit with trace_all=False — only filter consumer matches
        event = RuntimeEvent(
            type="filter.chain.executed",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="filter", component="chain"),
            data={"test": True},
            level="DEBUG",
        )
        dispatcher.emit(event)
        assert len(streaming_received) == 0
        assert len(filter_received) == 1

        # Toggle trace_all=True
        dispatcher.trace_all = True

        # Emit again — now both consumers receive
        streaming_received.clear()
        filter_received.clear()
        dispatcher.emit(event)
        assert len(streaming_received) == 1
        assert len(filter_received) == 1


class TestEventDispatcherReqId:
    """Test req_id handling (INV-06)."""

    def test_req_id_property(self):
        """req_id property is readable."""
        dispatcher = EventDispatcher(req_id="abc123")
        assert dispatcher.req_id == "abc123"

    def test_req_id_none_by_default(self):
        """req_id is None by default."""
        dispatcher = EventDispatcher()
        assert dispatcher.req_id is None


class TestEventDispatcherEmitAsync:
    """Test async emission path."""

    def test_emit_async_path(self):
        """emit_async() awaits async consumers with failure isolation."""
        dispatcher = EventDispatcher()
        received = []

        async def async_consumer(event):
            received.append(event)

        dispatcher.subscribe_async("streaming", async_consumer)
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )

        async def run():
            await dispatcher.emit_async(event)

        asyncio.run(run())
        assert len(received) == 1
        assert received[0] is event

    def test_emit_async_sync_consumer(self):
        """emit_async() also calls sync consumers."""
        dispatcher = EventDispatcher()
        received = []

        def sync_consumer(event):
            received.append(event)

        dispatcher.subscribe("streaming", sync_consumer)
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )

        async def run():
            await dispatcher.emit_async(event)

        asyncio.run(run())
        assert len(received) == 1

    def test_emit_async_failure_isolation(self):
        """emit_async() isolates async consumer failures."""
        dispatcher = EventDispatcher()
        results = []

        async def good_consumer(event):
            results.append("good")

        async def bad_consumer(event):
            raise ValueError("boom")

        dispatcher.subscribe_async("streaming", good_consumer)
        dispatcher.subscribe_async("streaming", bad_consumer)
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
            level="DEBUG",
        )

        async def run():
            await dispatcher.emit_async(event)

        asyncio.run(run())
        assert "good" in results


class TestEventDispatcherHandlerEvents:
    """Test handler event emission (streaming.handler.entry/exit)."""

    def test_handler_entry_event(self):
        """handler.entry events are emitted with correct type and data."""
        dispatcher = EventDispatcher(req_id="req-abc")
        received = []

        def consumer(event):
            received.append(event)

        dispatcher.subscribe("streaming", consumer)
        event = RuntimeEvent(
            type="streaming.handler.entry",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="handler"),
            data={
                "route": "test-route",
                "stream": True,
            },
            req_id="req-abc",
            level="INFO",
        )
        dispatcher.emit(event)
        assert len(received) == 1
        assert received[0].type == "streaming.handler.entry"
        assert received[0].req_id == "req-abc"
        assert received[0].data["route"] == "test-route"

    def test_handler_exit_event(self):
        """handler.exit events are emitted with correct type and data."""
        dispatcher = EventDispatcher(req_id="req-abc")
        received = []

        def consumer(event):
            received.append(event)

        dispatcher.subscribe("streaming", consumer)
        event = RuntimeEvent(
            type="streaming.handler.exit",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="handler"),
            data={
                "elapsed_ms": 42.5,
            },
            req_id="req-abc",
            level="INFO",
        )
        dispatcher.emit(event)
        assert len(received) == 1
        assert received[0].type == "streaming.handler.exit"
        assert received[0].data["elapsed_ms"] == 42.5


class TestRuntimeEvent:
    """Test RuntimeEvent envelope."""

    def test_minimal_construction(self):
        """RuntimeEvent can be constructed with minimal required fields."""
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"test": True},
        )
        assert event.type == "streaming.parser.event"
        assert event.level == "INFO"
        assert event.req_id is None

    def test_validation_invalid_type(self):
        """RuntimeEvent rejects type without dot separator."""
        with pytest.raises(ValueError):
            RuntimeEvent(
                type="invalid",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="streaming", component="parser"),
                data={},
            )

    def test_validation_invalid_level(self):
        """RuntimeEvent rejects invalid level."""
        with pytest.raises(ValueError):
            RuntimeEvent(
                type="streaming.parser.event",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="streaming", component="parser"),
                data={},
                level="WARNING",
            )

    def test_validation_data_must_be_dict(self):
        """RuntimeEvent rejects non-dict data."""
        with pytest.raises(TypeError):
            RuntimeEvent(
                type="streaming.parser.event",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="streaming", component="parser"),
                data="not a dict",
            )

    def test_frozen_immutable(self):
        """RuntimeEvent is frozen (shallow-immutable)."""
        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"key": "value"},
        )
        assert event.type == "streaming.parser.event"
        # Frozen dataclass: fields are immutable
        with pytest.raises(Exception):
            event.type = "new"

    def test_event_source_namespace(self):
        """EventSource.namespace derives hierarchical namespace."""
        source = EventSource(domain="streaming", component="parser")
        assert source.namespace == "streaming.parser"

    def test_event_source_namespace_with_instance(self):
        """EventSource.namespace includes instance when provided."""
        source = EventSource(
            domain="streaming",
            component="finalizer",
            instance="tool_call",
        )
        assert source.namespace == "streaming.finalizer.tool_call"


# ---------------------------------------------------------------------------
# C4/C5 Regression Tests — Option O1 Implementation
# ---------------------------------------------------------------------------


def _make_event(
    type_str: str = "streaming.parser.event",
    level: str = "DEBUG",
    req_id: str | None = None,
    data: dict | None = None,
) -> RuntimeEvent:
    """Helper to create a minimal RuntimeEvent."""
    return RuntimeEvent(
        type=type_str,
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="streaming", component="parser"),
        data=data or {"test": True},
        level=level,
        req_id=req_id,
    )


class TestC4_DispatcherNoFormatting:
    """C4 regression: Dispatcher performs no formatting.

    After Option O1, EventDispatcher always delivers RuntimeEvent.
    The formatting step is removed from emit()/emit_async().
    """

    def test_emit_delivers_runtime_event_not_str(self):
        """Dispatcher delivers RuntimeEvent, not str (C4 fix)."""
        from keeprollming.observability.dispatcher import EventDispatcher
        from keeprollming.observability.routing import RoutingEngine

        received: list = []
        consumer = lambda event: received.append(event)

        dispatcher = EventDispatcher(
            routing=RoutingEngine(),  # routing configured
        )
        dispatcher.subscribe("streaming", consumer)

        event = _make_event(type_str="streaming.parser.event")
        dispatcher.emit(event)

        assert len(received) == 1
        assert isinstance(received[0], RuntimeEvent)
        assert not isinstance(received[0], str)

    def test_emit_async_delivers_runtime_event_not_str(self):
        """emit_async delivers RuntimeEvent, not str (C4 fix)."""
        import asyncio
        from keeprollming.observability.dispatcher import EventDispatcher
        from keeprollming.observability.routing import RoutingEngine

        received: list = []
        async_consumer = lambda event: received.append(event)

        dispatcher = EventDispatcher(
            routing=RoutingEngine(),  # routing configured
        )
        dispatcher.subscribe("streaming", async_consumer)

        event = _make_event(type_str="streaming.parser.event")
        asyncio.run(dispatcher.emit_async(event))

        assert len(received) == 1
        assert isinstance(received[0], RuntimeEvent)
        assert not isinstance(received[0], str)

    def test_emit_without_routing_delivers_runtime_event(self):
        """Dispatcher without routing also delivers RuntimeEvent."""
        from keeprollming.observability.dispatcher import EventDispatcher

        received: list = []
        consumer = lambda event: received.append(event)

        dispatcher = EventDispatcher()  # no routing
        dispatcher.subscribe("streaming", consumer)

        event = _make_event(type_str="streaming.parser.event")
        dispatcher.emit(event)

        assert len(received) == 1
        assert isinstance(received[0], RuntimeEvent)


class TestC5_SubscriberContract:
    """C5 regression: Subscriber contract restored.

    After Option O1, subscribe() contract Callable[[RuntimeEvent], None]
    matches actual delivery. No # type: ignore needed.
    """

    def test_consumer_receives_runtime_event_with_routing(self):
        """Consumer declared as Callable[[RuntimeEvent], None] receives RuntimeEvent."""
        from keeprollming.observability.dispatcher import EventDispatcher
        from keeprollming.observability.routing import RoutingEngine

        received_type: type | None = None

        def typed_consumer(event: RuntimeEvent) -> None:
            nonlocal received_type
            received_type = type(event)

        dispatcher = EventDispatcher(
            routing=RoutingEngine(),
        )
        dispatcher.subscribe("streaming", typed_consumer)

        event = _make_event()
        dispatcher.emit(event)

        assert received_type is RuntimeEvent

    def test_logger_consumer_receives_runtime_event_with_routing(self):
        """LoggerConsumer receives RuntimeEvent when dispatcher has routing."""
        from keeprollming.observability.dispatcher import EventDispatcher
        from keeprollming.observability.routing import RoutingEngine

        consumer = LoggerConsumer(capture=True)
        dispatcher = EventDispatcher(
            routing=RoutingEngine(),
        )
        dispatcher.subscribe("streaming", consumer)

        event = _make_event()
        dispatcher.emit(event)

        assert len(consumer.captured) == 1
        assert isinstance(consumer.captured[0], RuntimeEvent)


class TestLoggerConsumerFormatting:
    """LoggerConsumer formatting ownership (Option O1)."""

    def test_consumer_formats_without_routing(self):
        """LoggerConsumer uses JsonFormatter fallback when no routing."""
        consumer = LoggerConsumer(capture=True)
        event = _make_event()
        consumer(event)

        assert len(consumer.captured) == 1
        # Should not raise — JsonFormatter fallback works

    def test_consumer_formats_with_routing(self):
        """LoggerConsumer uses RoutingEngine when configured."""
        from keeprollming.observability.routing import Route, RoutingEngine

        consumer = LoggerConsumer(
            capture=True,
            routing=RoutingEngine(),
        )
        event = _make_event()
        consumer(event)

        assert len(consumer.captured) == 1
        # Should not raise — RoutingEngine + JsonFormatter works

    def test_consumer_format_event_returns_json_string(self):
        """_format_event returns a JSON string with JsonFormatter."""
        consumer = LoggerConsumer(capture=True)
        event = _make_event()
        formatted = consumer._format_event(event)

        assert isinstance(formatted, str)
        parsed = json.loads(formatted)  # Should be valid JSON
        assert parsed["type"] == "streaming.parser.event"


# ---------------------------------------------------------------------------
# O5 Async Consumer Regression Tests — `formatted` defect correction
# ---------------------------------------------------------------------------


class TestO5_AsyncConsumerPath:
    """Regression tests for async consumer delivery in emit().

    The O5 correction moved formatting from Dispatcher to LoggerConsumer in the
    sync path, but left residual `formatted` logic in the async consumer block
    of emit(). This class exercises the async consumer path to prevent
    regression.
    """

    @pytest.mark.asyncio
    async def test_emit_async_consumer_receives_runtime_event(self):
        """emit() schedules async consumers with RuntimeEvent (not undefined `formatted`)."""
        received: list = []
        async def async_consumer(event: RuntimeEvent) -> None:
            received.append(event)

        dispatcher = EventDispatcher()
        dispatcher.subscribe_async("streaming", async_consumer)

        event = _make_event(type_str="streaming.parser.event")
        dispatcher.emit(event)

        # asyncio.create_task schedules on the running event loop;
        # await a short sleep to let the scheduled tasks complete
        await asyncio.sleep(0)

        assert len(received) == 1
        assert isinstance(received[0], RuntimeEvent)
        assert received[0] is event

    @pytest.mark.asyncio
    async def test_emit_async_consumer_with_routing_configured(self):
        """Async consumers work when RoutingEngine is configured."""
        from keeprollming.observability.routing import RoutingEngine

        received: list = []
        async def async_consumer(event: RuntimeEvent) -> None:
            received.append(event)

        dispatcher = EventDispatcher(routing=RoutingEngine())
        dispatcher.subscribe_async("streaming", async_consumer)

        event = _make_event(type_str="streaming.parser.event")
        dispatcher.emit(event)

        await asyncio.sleep(0)

        assert len(received) == 1
        assert isinstance(received[0], RuntimeEvent)

    @pytest.mark.asyncio
    async def test_emit_async_consumer_failure_isolation(self):
        """Async consumer failure does not block other consumers in emit()."""
        sync_results: list = []
        async_results: list = []

        def sync_consumer(event: RuntimeEvent) -> None:
            sync_results.append(event)

        async def bad_async_consumer(event: RuntimeEvent) -> None:
            raise ValueError("async boom")

        async def good_async_consumer(event: RuntimeEvent) -> None:
            async_results.append(event)

        dispatcher = EventDispatcher()
        dispatcher.subscribe("streaming", sync_consumer)
        dispatcher.subscribe_async("streaming", bad_async_consumer)
        dispatcher.subscribe_async("streaming", good_async_consumer)

        event = _make_event(type_str="streaming.parser.event")
        # Should not raise — bad async consumer isolated
        dispatcher.emit(event)

        # Sync consumer always fires immediately
        assert len(sync_results) == 1

        # Good async consumer should still fire despite bad one
        await asyncio.sleep(0)

        assert len(async_results) == 1
        assert isinstance(async_results[0], RuntimeEvent)

    @pytest.mark.asyncio
    async def test_emit_async_consumer_receives_event_not_str(self):
        """Async consumers in emit() receive RuntimeEvent, not str (C4 parity)."""
        received_types: list = []
        async def async_consumer(event: RuntimeEvent) -> None:
            received_types.append(type(event))

        dispatcher = EventDispatcher()
        dispatcher.subscribe_async("streaming", async_consumer)

        event = _make_event(type_str="streaming.parser.event")
        dispatcher.emit(event)

        await asyncio.sleep(0)

        assert len(received_types) == 1
        assert received_types[0] is RuntimeEvent
        assert received_types[0] is not str

    @pytest.mark.asyncio
    async def test_emit_async_consumer_with_trace_all(self):
        """Async consumers receive events even with trace_all=True."""
        received: list = []
        async def async_consumer(event: RuntimeEvent) -> None:
            received.append(event)

        dispatcher = EventDispatcher(trace_all=True)
        dispatcher.subscribe_async("filter", async_consumer)

        # Emit a streaming-scoped event — trace_all should reach filter consumer
        event = _make_event(type_str="streaming.parser.event")
        dispatcher.emit(event)

        await asyncio.sleep(0)

        assert len(received) == 1
        assert isinstance(received[0], RuntimeEvent)
