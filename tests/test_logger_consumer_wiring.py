"""Integration tests for production observability wiring.

The production path uses configured Projectors, not ``LoggerConsumer``.
These tests protect that architectural boundary after application startup.
"""

import time

import pytest
from fastapi import FastAPI

from keeprollming.app import get_event_dispatcher, lifespan
from keeprollming.observability.events import EventSource, RuntimeEvent


@pytest.mark.asyncio
async def test_projectors_not_legacy_logger_consumer_drive_production_output():
    """Lifespan activates configured projector subscriptions, not LoggerConsumer."""
    # Start the app lifespan to initialize EventDispatcher + consumers
    app = FastAPI(lifespan=lifespan)

    async with app.router.lifespan_context(app):
        dispatcher = get_event_dispatcher()
        assert dispatcher is not None, "EventDispatcher not initialized in lifespan"

        consumers_by_domain = dispatcher._consumers
        async_consumers_by_domain = dispatcher._async_consumers
        assert "execution.performance" in async_consumers_by_domain
        assert any(key.startswith("execution") for key in consumers_by_domain)
        assert not any(
            consumer.__class__.__name__ == "LoggerConsumer"
            for consumers in consumers_by_domain.values()
            for consumer in consumers
        )


@pytest.mark.asyncio
async def test_lifespan_dispatcher_delivers_execution_events_to_subscribers():
    """The initialized dispatcher remains usable for request-scoped consumers."""
    app = FastAPI(lifespan=lifespan)

    async with app.router.lifespan_context(app):
        dispatcher = get_event_dispatcher()
        assert dispatcher is not None

        received = []
        dispatcher.subscribe("execution.test", received.append)

        # Emit a test event
        test_event = RuntimeEvent(
            type="execution.test.wiring_check",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="execution", component="test"),
            data={"wiring_test": True},
            level="INFO",
            req_id="wiring-test-001",
        )
        dispatcher.emit(test_event)
        assert received == [test_event]
