"""Minimal EventDispatcher for KRM observability.

Defines the publication substrate that streaming pipeline producers
emit into.  The dispatcher owns fan-out; producers only call
``emit()`` / ``emit_async()`` on a known interface.

Invariants enforced here:
- INV-01: Unknown event types publishable without prior registration
- INV-02: TRACE_ALL bypasses all namespace/level filtering
- INV-08: Dispatcher is the minimal publication substrate
- INV-09: Producers emit to interface; dispatcher owns fan-out
- INV-10: Sync and async emission paths with failure isolation
- INV-11: Optional routing/formatter integration (Phase O5)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from .events import EventSource, RuntimeEvent
from .routing import RoutingEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# EventDispatcher
# ---------------------------------------------------------------------------


class EventDispatcher:
    """Minimal event dispatcher for KRM observability.

    Ownership:
    - Dispatcher is request-scoped or passed as parameter to producers
    - Producers emit to dispatcher interface; dispatcher owns fan-out
    - Failure isolation: one consumer failure does not block others
    - Sync and async emission paths supported
    """

    def __init__(
        self,
        trace_all: bool = False,
        req_id: Optional[str] = None,
        routing: Optional[RoutingEngine] = None,
    ) -> None:
        """Initialize dispatcher with empty consumer registry.

        Parameters
        ----------
        trace_all:
            When True, emit to ALL registered consumers regardless of
            their normal namespace/level filtering (INV-02).
        req_id:
            Optional request correlator. When present, injected into
            every RuntimeEvent's envelope (INV-06).
        routing:
            Optional RoutingEngine for formatter-based event routing.
            When configured, the RoutingEngine is available for LoggerConsumer
            to select and apply formatters (Option O1).
        """
        self._consumers: Dict[str, List[Callable[[RuntimeEvent], None]]] = {}
        self._async_consumers: Dict[str, List[Callable[[RuntimeEvent], Awaitable[None]]]] = {}
        self._trace_all = trace_all
        self._req_id = req_id
        self._routing = routing

    # ── Subscription ──────────────────────────────────────────────

    def subscribe(
        self,
        consumer_type: str,
        consumer_fn: Callable[[RuntimeEvent], None],
    ) -> None:
        """Register a synchronous consumer for a specific event type namespace.

        Parameters
        ----------
        consumer_type:
            Namespace prefix (e.g., "streaming", "filter", "execution").
        consumer_fn:
            Function that accepts RuntimeEvent and returns None.
        """
        self._consumers.setdefault(consumer_type, []).append(consumer_fn)

    def subscribe_async(
        self,
        consumer_type: str,
        consumer_fn: Callable[[RuntimeEvent], Awaitable[None]],
    ) -> None:
        """Register an async consumer for a specific event type namespace.

        Parameters
        ----------
        consumer_type:
            Namespace prefix (e.g., "streaming", "filter", "execution").
        consumer_fn:
            Async function that accepts RuntimeEvent and returns None.
        """
        self._async_consumers.setdefault(consumer_type, []).append(consumer_fn)

    # ── Sync emission ─────────────────────────────────────────────

    def emit(self, event: RuntimeEvent) -> None:
        """Emit event to all subscribed consumers.

        - Matches event.type against registered consumer_type prefixes
          (e.g., "streaming" matches "streaming.parser.event")
        - Calls sync consumers synchronously
        - Schedules async consumers for async execution via
          ``asyncio.create_task``
        - Failure isolation: consumer exceptions are logged at DEBUG
          but do not block other consumers (INV-10)
        - Unknown event types are still published (INV-01)
        """
        # Determine matching consumer_type prefixes
        matching_sync = self._match_prefix(event.type)
        matching_async = self._match_prefix_async(event.type)

        # Emit to sync consumers with failure isolation
        for consumer_fn in matching_sync:
            try:
                consumer_fn(event)
            except Exception:
                logger.debug(
                    "Consumer error: type=%s consumer=%s",
                    event.type,
                    getattr(consumer_fn, "__name__", str(consumer_fn)),
                    exc_info=True,
                )

        # Schedule async consumers with failure isolation
        for consumer_fn in matching_async:
            try:
                asyncio.create_task(consumer_fn(event))
            except Exception:
                logger.debug(
                    "Async consumer creation error: type=%s consumer=%s",
                    event.type,
                    getattr(consumer_fn, "__name__", str(consumer_fn)),
                    exc_info=True,
                )

    # ── Async emission ────────────────────────────────────────────

    async def emit_async(self, event: RuntimeEvent) -> None:
        """Emit event to all subscribed consumers (async context).

        - Same logic as emit() but awaits async consumers
        - Used in async streaming paths
        """
        matching_sync = self._match_prefix(event.type)
        matching_async = self._match_prefix_async(event.type)

        # Emit to sync consumers with failure isolation
        for consumer_fn in matching_sync:
            try:
                consumer_fn(event)
            except Exception:
                logger.debug(
                    "Consumer error (async path): type=%s consumer=%s",
                    event.type,
                    getattr(consumer_fn, "__name__", str(consumer_fn)),
                    exc_info=True,
                )

        # Await async consumers with failure isolation
        for consumer_fn in matching_async:
            try:
                await consumer_fn(event)
            except Exception:
                logger.debug(
                    "Async consumer error: type=%s consumer=%s",
                    event.type,
                    getattr(consumer_fn, "__name__", str(consumer_fn)),
                    exc_info=True,
                )

    # ── Internal matching ─────────────────────────────────────────

    def _match_prefix(self, event_type: str) -> List[Callable[[RuntimeEvent], None]]:
        """Find all sync consumers whose consumer_type is a prefix of event_type.

        When trace_all is True, returns ALL registered sync consumers
        regardless of prefix matching (INV-02).

        Returns an empty list when no consumers match (drop silently, INV-02).
        """
        if self._trace_all:
            result: List[Callable[[RuntimeEvent], None]] = []
            for consumers in self._consumers.values():
                result.extend(consumers)
            return result
        result: List[Callable[[RuntimeEvent], None]] = []
        for prefix, consumers in self._consumers.items():
            if event_type.startswith(prefix + ".") or event_type == prefix:
                result.extend(consumers)
        return result

    def _match_prefix_async(self, event_type: str) -> List[Callable[[RuntimeEvent], Awaitable[None]]]:
        """Find all async consumers whose consumer_type is a prefix of event_type.

        When trace_all is True, returns ALL registered async consumers
        regardless of prefix matching (INV-02).
        """
        if self._trace_all:
            result: List[Callable[[RuntimeEvent], Awaitable[None]]] = []
            for consumers in self._async_consumers.values():
                result.extend(consumers)
            return result
        result: List[Callable[[RuntimeEvent], Awaitable[None]]] = []
        for prefix, consumers in self._async_consumers.items():
            if event_type.startswith(prefix + ".") or event_type == prefix:
                result.extend(consumers)
        return result

    # ── TRACE_ALL toggle ──────────────────────────────────────────

    @property
    def trace_all(self) -> bool:
        """Whether TRACE_ALL is enabled (INV-02)."""
        return self._trace_all

    @trace_all.setter
    def trace_all(self, value: bool) -> None:
        self._trace_all = value

    # ── Request correlator ────────────────────────────────────────

    @property
    def req_id(self) -> Optional[str]:
        """Request correlator injected into emitted events (INV-06)."""
        return self._req_id
