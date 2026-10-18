"""Deterministic contracts for per-route admission control."""

import asyncio

import pytest

from keeprollming.admission import FallbackAdmission, RouteAdmissionController
from keeprollming.observability import EventDispatcher
from keeprollming.routing import Route
from keeprollming.routing.resources import backend_resource_key


@pytest.mark.asyncio
async def test_rejects_when_route_is_saturated_without_queueing() -> None:
    controller = RouteAdmissionController()
    dispatcher = EventDispatcher()
    events = []
    dispatcher.subscribe("request.lifecycle.admission", events.append)

    first = await controller.acquire(
        req_id="first",
        route_name="chat/limited",
        max_concurrent=1,
        queue_timeout=0,
        dispatcher=dispatcher,
    )
    assert first is not None
    rejected = await controller.acquire(
        req_id="second",
        route_name="chat/limited",
        max_concurrent=1,
        queue_timeout=0,
        dispatcher=dispatcher,
    )

    assert rejected is None
    assert [event.type for event in events] == [
        "request.lifecycle.admission.acquired",
        "request.lifecycle.admission.queued",
        "request.lifecycle.admission.rejected",
    ]
    await first.release()


@pytest.mark.asyncio
async def test_bounded_queue_acquires_after_a_slot_is_released() -> None:
    controller = RouteAdmissionController()
    first = await controller.acquire(
        req_id="first",
        route_name="chat/limited",
        max_concurrent=1,
        queue_timeout=0,
    )
    assert first is not None

    waiting = asyncio.create_task(
        controller.acquire(
            req_id="second",
            route_name="chat/limited",
            max_concurrent=1,
            queue_timeout=0.5,
        )
    )
    await asyncio.sleep(0)
    assert not waiting.done()
    await first.release()
    second = await waiting
    assert second is not None
    await second.release()


@pytest.mark.asyncio
async def test_unlimited_route_does_not_create_a_gate() -> None:
    controller = RouteAdmissionController()
    assert (
        await controller.acquire(
            req_id="unlimited",
            route_name="chat/unlimited",
            max_concurrent=None,
            queue_timeout=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_capacity_hint_tracks_a_held_route_slot_without_emitting_events() -> None:
    controller = RouteAdmissionController()
    assert controller.has_available_capacity(
        route_name="chat/limited",
        max_concurrent=1,
    )


@pytest.mark.asyncio
async def test_routes_sharing_resource_key_share_one_capacity_gate() -> None:
    controller = RouteAdmissionController()
    resource = "http://backend:8000 :: qwen"
    first = await controller.acquire(
        req_id="first",
        route_name="chat/deep",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert first is not None
    assert not controller.has_available_capacity(
        route_name="code/senior",
        max_concurrent=1,
        resource_key=resource,
    )
    second = await controller.acquire(
        req_id="second",
        route_name="code/senior",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert second is None
    await first.release()
    lease = await controller.acquire(
        req_id="first",
        route_name="chat/limited",
        max_concurrent=1,
        queue_timeout=0,
    )
    assert lease is not None
    assert not controller.has_available_capacity(
        route_name="chat/limited",
        max_concurrent=1,
    )
    await lease.release()
    assert controller.has_available_capacity(
        route_name="chat/limited",
        max_concurrent=1,
    )


@pytest.mark.asyncio
async def test_higher_priority_shared_resource_waiter_runs_first() -> None:
    controller = RouteAdmissionController()
    resource = "http://backend:8000 :: qwen"
    active = await controller.acquire(
        req_id="active",
        route_name="public/code",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert active is not None

    low = asyncio.create_task(
        controller.acquire(
            req_id="low",
            route_name="public/code",
            max_concurrent=1,
            queue_timeout=1,
            routing_priority=0,
            resource_key=resource,
        )
    )
    await asyncio.sleep(0)
    high = asyncio.create_task(
        controller.acquire(
            req_id="high",
            route_name="code/priority",
            max_concurrent=1,
            queue_timeout=1,
            routing_priority=100,
            resource_key=resource,
        )
    )
    await asyncio.sleep(0)

    await active.release()
    high_lease = await high
    assert high_lease is not None
    assert not low.done()
    await high_lease.release()

    low_lease = await low
    assert low_lease is not None
    await low_lease.release()


@pytest.mark.asyncio
async def test_queue_aging_promotes_a_waiter_without_preempting_a_live_lease() -> None:
    """A sufficiently old lower-class request may win only the next slot."""
    now = 0.0
    controller = RouteAdmissionController(clock=lambda: now)
    resource = "http://backend:8000 :: qwen"
    active = await controller.acquire(
        req_id="active",
        route_name="code/active",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert active is not None

    aged = asyncio.create_task(
        controller.acquire(
            req_id="aged",
            route_name="public/aged",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=0,
            resource_key=resource,
            queue_aging={
                "interval_seconds": 1,
                "priority_step": 50,
                "max_boost": 100,
            },
        )
    )
    await asyncio.sleep(0)
    now = 2.0
    high = asyncio.create_task(
        controller.acquire(
            req_id="high",
            route_name="code/high",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=90,
            resource_key=resource,
        )
    )
    await asyncio.sleep(0)

    # The active request remains undisturbed. Once it releases, aging turns
    # the old request's effective priority into 100, ahead of 90.
    await active.release()
    aged_lease = await aged
    assert aged_lease is not None
    assert not high.done()
    await aged_lease.release()

    high_lease = await high
    assert high_lease is not None
    await high_lease.release()


@pytest.mark.asyncio
async def test_queue_aging_is_capped_and_preserves_fifo_on_equal_priority() -> None:
    now = 0.0
    controller = RouteAdmissionController(clock=lambda: now)
    resource = "http://backend:8000 :: qwen"
    active = await controller.acquire(
        req_id="active",
        route_name="code/active",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert active is not None

    first = asyncio.create_task(
        controller.acquire(
            req_id="first",
            route_name="public/first",
            max_concurrent=1,
            queue_timeout=10,
            resource_key=resource,
            queue_aging={"interval_seconds": 1, "priority_step": 100, "max_boost": 5},
        )
    )
    await asyncio.sleep(0)
    second = asyncio.create_task(
        controller.acquire(
            req_id="second",
            route_name="public/second",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=5,
            resource_key=resource,
        )
    )
    await asyncio.sleep(0)
    now = 100.0

    # The first request caps at 5, ties the second, and FIFO wins.
    await active.release()
    first_lease = await first
    assert first_lease is not None
    assert not second.done()
    await first_lease.release()
    second_lease = await second
    assert second_lease is not None
    await second_lease.release()


@pytest.mark.asyncio
async def test_queue_aging_events_expose_policy_and_effective_priority() -> None:
    now = 0.0
    controller = RouteAdmissionController(clock=lambda: now)
    dispatcher = EventDispatcher()
    events = []
    dispatcher.subscribe("request.lifecycle.admission", events.append)
    active = await controller.acquire(
        req_id="active",
        route_name="chat/limited",
        max_concurrent=1,
        queue_timeout=0,
        dispatcher=dispatcher,
    )
    assert active is not None
    waiting = asyncio.create_task(
        controller.acquire(
            req_id="waiting",
            route_name="chat/limited",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=4,
            queue_aging={"interval_seconds": 1, "priority_step": 3, "max_boost": 6},
            dispatcher=dispatcher,
        )
    )
    await asyncio.sleep(0)
    now = 2.0
    await active.release()
    lease = await waiting
    assert lease is not None

    queued, acquired = [event for event in events if event.req_id == "waiting"]
    assert queued.data["queue_aging_interval_ms"] == 1000
    assert queued.data["queue_aging_priority_step"] == 3
    assert queued.data["queue_aging_max_boost"] == 6
    assert acquired.data["effective_routing_priority"] == 10
    assert acquired.data["queue_wait_ms"] == 2000
    await lease.release()


@pytest.mark.asyncio
async def test_feasible_deadline_pressure_can_promote_a_queued_request() -> None:
    now = 0.0
    controller = RouteAdmissionController(clock=lambda: now)
    resource = "http://backend:8000 :: qwen"
    active = await controller.acquire(
        req_id="active",
        route_name="code/active",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert active is not None

    urgent = asyncio.create_task(
        controller.acquire(
            req_id="urgent",
            route_name="public/urgent",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=0,
            resource_key=resource,
            deadline_policy={
                "enabled": True,
                "first_token_budget_ms": 10000,
                "urgency_window_ms": 5000,
                "max_priority_boost": 20,
                "ttft_safety_factor": 1.0,
                "min_samples": 1,
            },
            first_token_target_at=10.0,
            estimated_ttft_ms=5000,
        )
    )
    await asyncio.sleep(0)
    normal = asyncio.create_task(
        controller.acquire(
            req_id="normal",
            route_name="code/normal",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=10,
            resource_key=resource,
        )
    )
    await asyncio.sleep(0)

    now = 4.0  # Estimated slack: one second, within the urgency window.
    await active.release()
    urgent_lease = await urgent
    assert urgent_lease is not None
    assert not normal.done()
    await urgent_lease.release()
    normal_lease = await normal
    assert normal_lease is not None
    await normal_lease.release()


@pytest.mark.asyncio
async def test_deadline_policy_without_ttft_evidence_remains_neutral() -> None:
    now = 0.0
    controller = RouteAdmissionController(clock=lambda: now)
    resource = "http://backend:8000 :: qwen"
    active = await controller.acquire(
        req_id="active",
        route_name="code/active",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert active is not None

    uncertain = asyncio.create_task(
        controller.acquire(
            req_id="uncertain",
            route_name="public/uncertain",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=0,
            resource_key=resource,
            deadline_policy={"enabled": True, "first_token_budget_ms": 10000},
            first_token_target_at=10.0,
            estimated_ttft_ms=None,
        )
    )
    await asyncio.sleep(0)
    high = asyncio.create_task(
        controller.acquire(
            req_id="high",
            route_name="code/high",
            max_concurrent=1,
            queue_timeout=10,
            routing_priority=10,
            resource_key=resource,
        )
    )
    await asyncio.sleep(0)

    now = 4.0
    await active.release()
    high_lease = await high
    assert high_lease is not None
    assert not uncertain.done()
    await high_lease.release()
    uncertain_lease = await uncertain
    assert uncertain_lease is not None
    await uncertain_lease.release()


@pytest.mark.asyncio
async def test_shared_resource_snapshot_reports_aggregate_gate_state() -> None:
    controller = RouteAdmissionController()
    resource = "http://backend:8000 :: qwen"
    lease = await controller.acquire(
        req_id="active",
        route_name="chat/a",
        max_concurrent=1,
        queue_timeout=0,
        resource_key=resource,
    )
    assert lease is not None

    assert controller.snapshot(
        route_name="chat/b",
        max_concurrent=1,
        resource_key=resource,
    ) == {"capacity": 1, "active": 1, "queued": 0}
    await lease.release()


@pytest.mark.asyncio
async def test_fallback_transfer_releases_failed_resource_and_holds_target() -> None:
    """A fallback must occupy its actual backend, not its failed origin."""
    controller = RouteAdmissionController()
    primary = Route(
        name="runner/primary",
        pattern="runner/primary",
        model="primary",
        upstream_url="http://primary:8000",
        max_concurrent=1,
    )
    secondary = Route(
        name="runner/secondary",
        pattern="runner/secondary",
        model="secondary",
        upstream_url="http://secondary:8000",
        max_concurrent=1,
    )
    primary_key = str(backend_resource_key(primary))
    secondary_key = str(backend_resource_key(secondary))
    primary_lease = await controller.acquire(
        req_id="request",
        route_name=primary.name,
        max_concurrent=1,
        queue_timeout=0,
        resource_key=primary_key,
    )
    assert primary_lease is not None

    admission = FallbackAdmission(
        controller=controller,
        req_id="request",
        lease=primary_lease,
        route_name=primary.name,
        max_concurrent=1,
        resource_key=primary_key,
        queue_timeout=0,
        routing_priority=0,
        service_route_name="public/code",
    )

    assert await admission.move_to(secondary) is True
    assert controller.has_available_capacity(
        route_name=primary.name,
        max_concurrent=1,
        resource_key=primary_key,
    )
    assert not controller.has_available_capacity(
        route_name=secondary.name,
        max_concurrent=1,
        resource_key=secondary_key,
    )

    await admission.release()
    assert controller.has_available_capacity(
        route_name=secondary.name,
        max_concurrent=1,
        resource_key=secondary_key,
    )


@pytest.mark.asyncio
async def test_fallback_transfer_does_not_overcommit_saturated_target() -> None:
    """A rejected fallback frees the origin and leaves the target unchanged."""
    controller = RouteAdmissionController()
    primary = Route(
        name="runner/primary",
        pattern="runner/primary",
        model="primary",
        upstream_url="http://primary:8000",
        max_concurrent=1,
    )
    secondary = Route(
        name="runner/secondary",
        pattern="runner/secondary",
        model="secondary",
        upstream_url="http://secondary:8000",
        max_concurrent=1,
    )
    primary_key = str(backend_resource_key(primary))
    secondary_key = str(backend_resource_key(secondary))
    primary_lease = await controller.acquire(
        req_id="request",
        route_name=primary.name,
        max_concurrent=1,
        queue_timeout=0,
        resource_key=primary_key,
    )
    blocker = await controller.acquire(
        req_id="other",
        route_name=secondary.name,
        max_concurrent=1,
        queue_timeout=0,
        resource_key=secondary_key,
    )
    assert primary_lease is not None
    assert blocker is not None

    admission = FallbackAdmission(
        controller=controller,
        req_id="request",
        lease=primary_lease,
        route_name=primary.name,
        max_concurrent=1,
        resource_key=primary_key,
        queue_timeout=0,
        routing_priority=0,
        service_route_name="public/code",
    )

    assert await admission.move_to(secondary) is False
    assert controller.has_available_capacity(
        route_name=primary.name,
        max_concurrent=1,
        resource_key=primary_key,
    )
    assert not controller.has_available_capacity(
        route_name=secondary.name,
        max_concurrent=1,
        resource_key=secondary_key,
    )
    # A later logical route resolving to the same saturated resource must not
    # mistake the rejected transfer for a lease it already owns.
    same_secondary_resource = Route(
        name="runner/secondary-alias",
        pattern="runner/secondary-alias",
        model="secondary",
        upstream_url="http://secondary:8000",
        max_concurrent=1,
    )
    assert await admission.move_to(same_secondary_resource) is False
    await admission.release()
    await blocker.release()
