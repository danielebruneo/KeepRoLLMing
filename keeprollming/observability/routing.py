"""Routing engine for KRM observability.

Defines ``Route`` and ``RoutingEngine`` for priority-based event
routing to formatters.

Routing rules:
- Route rules are configurable: match event type prefix → route to specific formatter
- Default route: JsonFormatter for all unmatched events
- Routes are evaluated in priority order (lower number = higher priority)
- First matching route wins

The routing engine is **request-scoped** (new instance per request)
and **synchronous** (< 1ms per lookup).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .events import RuntimeEvent
from .formatters import Formatter, JsonFormatter, FORMAT_ERROR


@dataclass(frozen=True)
class Route:
    """A single routing rule.

    Parameters
    ----------
    priority:
        Lower number = higher priority. Routes are evaluated
        in ascending priority order. First match wins.
    event_prefix:
        Prefix to match against ``RuntimeEvent.type``.
        Uses ``str.startswith()`` matching (same semantics
        as dispatcher prefix matching).
    formatter:
        The Formatter instance to use for matching events.
    """
    priority: int
    event_prefix: str
    formatter: Formatter


class RoutingEngine:
    """Priority-based event routing engine.

    Ownership:
    - RoutingEngine is request-scoped (new instance per request)
    - Routes are evaluated in priority order (lower number = higher priority)
    - First matching route wins
    - Default route returns JsonFormatter for unmatched events

    Contract:
    - Synchronous, fast (< 1ms per lookup)
    - Request-scoped (new instance per request)
    - Default formatter is JsonFormatter
    """

    def __init__(self) -> None:
        """Initialize routing engine with empty route list."""
        self._routes: List[Route] = []
        self._default_formatter: Formatter = JsonFormatter()

    def add_route(self, route: Route) -> None:
        """Add a routing rule.

        Routes are stored in insertion order and sorted by priority
        on each lookup to ensure correct ordering.

        Parameters
        ----------
        route:
            The Route to add.
        """
        self._routes.append(route)

    def get_formatter(self, event: RuntimeEvent) -> Formatter:
        """Find the formatter for an event using priority-based matching.

        Parameters
        ----------
        event:
            The RuntimeEvent to route.

        Returns
        -------
        Formatter
            The first matching formatter, or the default JsonFormatter.
        """
        # Sort routes by priority (lower number = higher priority)
        sorted_routes = sorted(self._routes, key=lambda r: r.priority)

        for route in sorted_routes:
            prefix = route.event_prefix
            if event.type.startswith(prefix + ".") or event.type == prefix:
                return route.formatter

        return self._default_formatter

    def clear_routes(self) -> None:
        """Remove all registered routes.

        After clearing, all events route to the default formatter.
        """
        self._routes.clear()

    @property
    def route_count(self) -> int:
        """Number of registered routes."""
        return len(self._routes)

    @property
    def default_formatter(self) -> Formatter:
        """The default formatter for unmatched events."""
        return self._default_formatter
