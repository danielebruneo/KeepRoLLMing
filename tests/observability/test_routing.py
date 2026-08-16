"""Unit tests for observability routing engine (Phase O5)."""

import time

import pytest

from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.formatters import (
    CompactFormatter,
    JsonFormatter,
    PlainTextFormatter,
)
from keeprollming.observability.routing import Route, RoutingEngine


def _make_event(
    event_type: str = "streaming.parser.event",
    data: dict | None = None,
) -> RuntimeEvent:
    """Helper to create a minimal RuntimeEvent for testing."""
    return RuntimeEvent(
        type=event_type,
        timestamp_ns=time.time_ns(),
        source=EventSource(domain="streaming", component="parser"),
        data=data or {"key": "value"},
    )


class TestRoute:
    """Test Route dataclass."""

    def test_route_creation(self):
        """Route can be created with priority, prefix, and formatter."""
        route = Route(
            priority=10,
            event_prefix="streaming",
            formatter=PlainTextFormatter(),
        )
        assert route.priority == 10
        assert route.event_prefix == "streaming"
        assert isinstance(route.formatter, PlainTextFormatter)

    def test_route_is_frozen(self):
        """Route is frozen (immutable)."""
        route = Route(
            priority=10,
            event_prefix="streaming",
            formatter=PlainTextFormatter(),
        )
        with pytest.raises(Exception):
            route.priority = 20


class TestRoutingEngine:
    """Test RoutingEngine behavior."""

    def test_routing_first_match_wins(self):
        """First matching route (by priority) wins."""
        engine = RoutingEngine()
        engine.add_route(Route(priority=20, event_prefix="streaming", formatter=CompactFormatter()))
        engine.add_route(Route(priority=10, event_prefix="streaming", formatter=PlainTextFormatter()))

        event = _make_event("streaming.parser.event")
        formatter = engine.get_formatter(event)

        # Priority 10 (PlainTextFormatter) should win over priority 20
        assert isinstance(formatter, PlainTextFormatter)

    def test_routing_default_formatter(self):
        """Unmatched events route to JsonFormatter (default)."""
        engine = RoutingEngine()
        engine.add_route(Route(priority=10, event_prefix="filter", formatter=CompactFormatter()))

        event = _make_event("streaming.parser.event")
        formatter = engine.get_formatter(event)

        assert isinstance(formatter, JsonFormatter)

    def test_routing_no_routes(self):
        """Empty routes → default JsonFormatter."""
        engine = RoutingEngine()
        event = _make_event("any.event.type")
        formatter = engine.get_formatter(event)

        assert isinstance(formatter, JsonFormatter)

    def test_routing_multiple_prefixes(self):
        """Different prefixes → different formatters."""
        engine = RoutingEngine()
        engine.add_route(Route(priority=10, event_prefix="streaming", formatter=CompactFormatter()))
        engine.add_route(Route(priority=10, event_prefix="filter", formatter=PlainTextFormatter()))
        engine.add_route(Route(priority=10, event_prefix="execution", formatter=JsonFormatter()))

        # streaming → CompactFormatter
        assert isinstance(
            engine.get_formatter(_make_event("streaming.parser.event")),
            CompactFormatter,
        )
        # filter → PlainTextFormatter
        assert isinstance(
            engine.get_formatter(_make_event("filter.chain.executed")),
            PlainTextFormatter,
        )
        # execution → JsonFormatter
        assert isinstance(
            engine.get_formatter(_make_event("execution.request.started")),
            JsonFormatter,
        )
        # unmatched → JsonFormatter (default)
        assert isinstance(
            engine.get_formatter(_make_event("unknown.event.type")),
            JsonFormatter,
        )

    def test_routing_priority_order(self):
        """Lower priority number = higher priority (evaluated first)."""
        engine = RoutingEngine()
        # Priority 30 — lowest priority (evaluated last)
        engine.add_route(Route(priority=30, event_prefix="streaming", formatter=CompactFormatter()))
        # Priority 10 — highest priority (evaluated first)
        engine.add_route(Route(priority=10, event_prefix="streaming", formatter=PlainTextFormatter()))
        # Priority 20 — middle
        engine.add_route(Route(priority=20, event_prefix="streaming", formatter=JsonFormatter()))

        event = _make_event("streaming.parser.event")
        formatter = engine.get_formatter(event)

        # Priority 10 (PlainTextFormatter) should win
        assert isinstance(formatter, PlainTextFormatter)

    def test_routing_exact_match(self):
        """Exact prefix match (not just substring)."""
        engine = RoutingEngine()
        engine.add_route(Route(priority=10, event_prefix="streaming", formatter=CompactFormatter()))

        # Substring match (streaming.parser starts with "streaming.")
        assert isinstance(
            engine.get_formatter(_make_event("streaming.parser.event")),
            CompactFormatter,
        )
        # Non-match (streaming2 does not start with "streaming.")
        assert isinstance(
            engine.get_formatter(_make_event("streaming2.event")),
            JsonFormatter,
        )

    def test_routing_clear_routes(self):
        """clear_routes() removes all routes, falls back to default."""
        engine = RoutingEngine()
        engine.add_route(Route(priority=10, event_prefix="streaming", formatter=CompactFormatter()))
        assert engine.route_count == 1

        engine.clear_routes()
        assert engine.route_count == 0

        event = _make_event("streaming.parser.event")
        formatter = engine.get_formatter(event)
        assert isinstance(formatter, JsonFormatter)

    def test_routing_route_count(self):
        """route_count reflects the number of registered routes."""
        engine = RoutingEngine()
        assert engine.route_count == 0

        engine.add_route(Route(priority=10, event_prefix="streaming", formatter=CompactFormatter()))
        assert engine.route_count == 1

        engine.add_route(Route(priority=20, event_prefix="filter", formatter=PlainTextFormatter()))
        assert engine.route_count == 2

    def test_routing_default_formatter_property(self):
        """default_formatter property returns JsonFormatter."""
        engine = RoutingEngine()
        assert isinstance(engine.default_formatter, JsonFormatter)
