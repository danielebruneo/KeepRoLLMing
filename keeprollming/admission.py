"""Per-resource, event-loop-local admission control.

The shared HTTP pool limits sockets, not the work allowed to reach a backend.
This module owns the earlier boundary: all routes resolving to one backend
resource share one gate. When work is queued, higher service-class priority
wins; equal priorities retain FIFO order. A live lease is never preempted.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .observability import events_request


@dataclass(frozen=True)
class QueueAgingPolicy:
    """Bounded priority promotion for one request waiting at a gate.

    This policy is deliberately attached to the waiting request rather than to
    the shared resource: several client-facing routes may legitimately use
    different service classes and starvation tolerances for one backend.
    """

    interval_seconds: float = 30.0
    priority_step: int = 1
    max_boost: int = 20


@dataclass(frozen=True)
class DeadlinePolicy:
    """Server-owned, confidence-gated first-token scheduling policy.

    It is intentionally advisory: without sufficient historical TTFT samples,
    it produces no boost. ``queue_timeout`` remains the hard admission limit.
    """

    enabled: bool = False
    first_token_budget_seconds: float | None = None
    urgency_window_seconds: float = 5.0
    max_priority_boost: int = 20
    ttft_safety_factor: float = 1.5
    min_samples: int = 5


def parse_queue_aging(value: Any) -> QueueAgingPolicy | None:
    """Normalize one route's optional queue-aging configuration.

    ``None`` disables aging.  A mapping enables it and may override any of
    the bounded defaults.  Keeping the parser here means runtime behavior and
    configuration validation share precisely the same contract.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("expected a mapping or null")

    allowed = {"interval_seconds", "priority_step", "max_boost"}
    unsupported = sorted(str(key) for key in set(value) - allowed)
    if unsupported:
        raise ValueError(f"unsupported key(s) {', '.join(unsupported)}")

    interval = value.get("interval_seconds", 30.0)
    step = value.get("priority_step", 1)
    max_boost = value.get("max_boost", 20)
    try:
        interval_value = float(interval)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval_seconds must be a positive finite number") from exc
    if isinstance(interval, bool) or not math.isfinite(interval_value) or interval_value <= 0:
        raise ValueError("interval_seconds must be a positive finite number")
    if isinstance(step, bool) or not isinstance(step, int) or step <= 0:
        raise ValueError("priority_step must be a positive integer")
    if isinstance(max_boost, bool) or not isinstance(max_boost, int) or max_boost < 0:
        raise ValueError("max_boost must be a non-negative integer")
    return QueueAgingPolicy(
        interval_seconds=interval_value,
        priority_step=step,
        max_boost=max_boost,
    )


def parse_deadline_policy(value: Any) -> DeadlinePolicy:
    """Normalize the optional route-owned first-token scheduling policy."""
    if value is None:
        return DeadlinePolicy()
    if not isinstance(value, Mapping):
        raise ValueError("deadline_policy must be a mapping or null")

    allowed = {
        "enabled",
        "first_token_budget_ms",
        "urgency_window_ms",
        "max_priority_boost",
        "ttft_safety_factor",
        "min_samples",
    }
    unsupported = sorted(str(key) for key in set(value) - allowed)
    if unsupported:
        raise ValueError(f"deadline_policy has unsupported key(s) {', '.join(unsupported)}")

    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("deadline_policy.enabled must be boolean")

    def positive_number(key: str, default: float | None, *, required: bool = False) -> float | None:
        raw = value.get(key, default)
        if raw is None:
            if required:
                raise ValueError(f"deadline_policy.{key} is required when enabled")
            return None
        try:
            parsed = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"deadline_policy.{key} must be a positive finite number") from exc
        if isinstance(raw, bool) or not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(f"deadline_policy.{key} must be a positive finite number")
        return parsed

    budget_ms = positive_number(
        "first_token_budget_ms",
        None,
        required=enabled,
    )
    urgency_ms = positive_number("urgency_window_ms", 5000.0)
    safety_factor = positive_number("ttft_safety_factor", 1.5)
    max_boost = value.get("max_priority_boost", 20)
    min_samples = value.get("min_samples", 5)
    if isinstance(max_boost, bool) or not isinstance(max_boost, int) or max_boost < 0:
        raise ValueError("deadline_policy.max_priority_boost must be a non-negative integer")
    if isinstance(min_samples, bool) or not isinstance(min_samples, int) or min_samples <= 0:
        raise ValueError("deadline_policy.min_samples must be a positive integer")
    return DeadlinePolicy(
        enabled=enabled,
        first_token_budget_seconds=(budget_ms / 1000.0 if budget_ms is not None else None),
        urgency_window_seconds=float(urgency_ms) / 1000.0,
        max_priority_boost=max_boost,
        ttft_safety_factor=float(safety_factor),
        min_samples=min_samples,
    )


@dataclass
class _Waiter:
    priority: int
    order: int
    future: asyncio.Future[None]
    enqueued_at: float
    aging: QueueAgingPolicy | None = None
    deadline_policy: DeadlinePolicy = field(default_factory=DeadlinePolicy)
    first_token_target_at: float | None = None
    estimated_ttft_seconds: float | None = None
    granted: bool = False

    def effective_priority(self, now: float) -> int:
        """Return the bounded promotion due at ``now`` without mutation."""
        aging_boost = 0
        if self.aging is not None and self.aging.max_boost != 0:
            intervals = max(
                0,
                int((now - self.enqueued_at) // self.aging.interval_seconds),
            )
            aging_boost = min(
                self.aging.max_boost,
                intervals * self.aging.priority_step,
            )
        return self.priority + aging_boost + self.deadline_boost(now)

    def deadline_boost(self, now: float) -> int:
        """Return urgency pressure only for a still-feasible first-token SLO."""
        policy = self.deadline_policy
        if (
            not policy.enabled
            or policy.max_priority_boost == 0
            or self.first_token_target_at is None
            or self.estimated_ttft_seconds is None
        ):
            return 0
        slack = self.first_token_target_at - now - self.estimated_ttft_seconds * policy.ttft_safety_factor
        # An already infeasible target receives no special treatment: this
        # prevents hopeless work from permanently jumping the queue.
        if slack < 0 or slack > policy.urgency_window_seconds:
            return 0
        pressure = 1.0 - (slack / policy.urgency_window_seconds)
        return min(policy.max_priority_boost, math.ceil(pressure * policy.max_priority_boost))


@dataclass
class _AdmissionGate:
    """A bounded, deterministic priority gate bound to one event loop."""

    capacity: int
    active: int = 0
    _next_order: int = 0
    _waiters: list[_Waiter] = field(default_factory=list)
    _clock: Callable[[], float] = time.monotonic

    @property
    def has_available_capacity(self) -> bool:
        # Do not allow a new request to bypass queued work, even if a release
        # and a new arrival happen in adjacent event-loop turns.
        return self.active < self.capacity and not self._waiters

    def snapshot(self) -> dict[str, int]:
        """Return non-identifying state for the shared backend resource."""
        return {
            "capacity": self.capacity,
            "active": self.active,
            "queued": len(self._waiters),
        }

    def acquire_or_enqueue(
        self,
        priority: int,
        aging: QueueAgingPolicy | None = None,
        deadline_policy: DeadlinePolicy | None = None,
        first_token_target_at: float | None = None,
        estimated_ttft_seconds: float | None = None,
    ) -> _Waiter | None:
        if self.has_available_capacity:
            self.active += 1
            return None
        waiter = _Waiter(
            priority=priority,
            order=self._next_order,
            future=asyncio.get_running_loop().create_future(),
            enqueued_at=self._clock(),
            aging=aging,
            deadline_policy=deadline_policy or DeadlinePolicy(),
            first_token_target_at=first_token_target_at,
            estimated_ttft_seconds=estimated_ttft_seconds,
        )
        self._next_order += 1
        self._waiters.append(waiter)
        return waiter

    def cancel(self, waiter: _Waiter) -> None:
        if waiter.granted:
            # The grant has already reserved active capacity. Its caller will
            # release it through the lease/cancellation path.
            return
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return
        if not waiter.future.done():
            waiter.future.cancel()

    def release(self) -> None:
        while self._waiters:
            # Higher effective priority first, FIFO among equal-priority
            # requests. Aging is evaluated only at this request boundary: it
            # never interrupts a live lease or reorders emitted data.
            now = self._clock()
            waiter = min(
                self._waiters,
                key=lambda item: (-item.effective_priority(now), item.order),
            )
            self._waiters.remove(waiter)
            if waiter.future.cancelled():
                continue
            waiter.granted = True
            waiter.future.set_result(None)
            return
        if self.active <= 0:
            raise RuntimeError("admission gate released without an active lease")
        self.active -= 1


@dataclass
class AdmissionLease:
    """One acquired resource slot; releasing it is idempotent."""

    route_name: str
    gate: _AdmissionGate
    dispatcher: Any = None
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        self.gate.release()
        events_request.emit_admission_released(self.route_name, dispatcher=self.dispatcher)


class FallbackAdmission:
    """Own admission while one request moves between fallback attempts.

    Route fallback is an execution concern, but backend capacity belongs to the
    concrete attempt currently allowed to contact an upstream.  Keeping the
    initial lease while retrying a different resource can overcommit that
    fallback backend; retaining both leases would instead leak capacity.  This
    small request-scoped holder releases the failed resource before acquiring
    the next one and remains the single object released at request completion.

    The transition is deliberately request-boundary/pre-output only.  Callers
    invoke :meth:`move_to` before opening a fallback connection, never after a
    stream has emitted client-visible data.
    """

    def __init__(
        self,
        *,
        controller: "RouteAdmissionController",
        req_id: str,
        lease: AdmissionLease | None,
        route_name: str,
        max_concurrent: int | None,
        resource_key: str | None,
        queue_timeout: float | None,
        routing_priority: int,
        queue_aging: Any = None,
        deadline_policy: Any = None,
        first_token_target_at: float | None = None,
        ttft_estimator: Callable[[str], float | None] | None = None,
        service_route_name: str | None,
        dispatcher: Any = None,
    ) -> None:
        self._controller = controller
        self._req_id = req_id
        self._lease = lease
        self._route_name = route_name
        self._max_concurrent = max_concurrent
        self._resource_key = resource_key
        self._queue_timeout = queue_timeout
        self._routing_priority = routing_priority
        self._queue_aging = queue_aging
        self._deadline_policy = deadline_policy
        self._first_token_target_at = first_token_target_at
        self._ttft_estimator = ttft_estimator
        self._service_route_name = service_route_name
        self._dispatcher = dispatcher

    @property
    def lease(self) -> AdmissionLease | None:
        """Current concrete-resource lease, if this route has a limit."""
        return self._lease

    async def move_to(self, route: Any) -> bool:
        """Transfer admission to ``route`` before a fallback attempt.

        ``False`` means the target rejected/expired its queue budget.  The
        caller may then continue to a later fallback candidate; the original
        resource has already been released and cannot remain falsely busy.
        """
        from .routing.resources import backend_resource_key

        target_resource = backend_resource_key(route)
        target_key = str(target_resource) if target_resource is not None else None
        target_name = str(getattr(route, "name", ""))
        target_max = getattr(route, "max_concurrent", None)

        # Multiple logical fallback routes can deliberately resolve to the
        # same physical backend.  Their existing lease already represents the
        # resource, so no release/reacquire (and no self-queue) is needed.
        if self._lease is not None and target_key == self._resource_key and target_max == self._max_concurrent:
            self._route_name = target_name
            return True

        if self._lease is not None:
            await self._lease.release()
        self._lease = None
        self._route_name = target_name
        self._max_concurrent = target_max
        self._resource_key = target_key

        lease = await self._controller.acquire(
            req_id=self._req_id,
            route_name=target_name,
            max_concurrent=target_max,
            queue_timeout=self._queue_timeout,
            routing_priority=self._routing_priority,
            queue_aging=self._queue_aging,
            deadline_policy=self._deadline_policy,
            first_token_target_at=self._first_token_target_at,
            estimated_ttft_ms=(self._ttft_estimator(target_name) if self._ttft_estimator is not None else None),
            service_route_name=self._service_route_name,
            resource_key=target_key,
            dispatcher=self._dispatcher,
        )
        if target_max is not None and lease is None:
            return False
        self._lease = lease
        return True

    async def release(self) -> None:
        """Release whichever resource currently owns this request's slot."""
        if self._lease is not None:
            await self._lease.release()
            self._lease = None


class RouteAdmissionController:
    """Own priority gates per event loop and resolved backend resource.

    Tests and embedders can create independent loops in one interpreter; the
    loop identity is therefore part of the key. In a normal KRM server, routes
    sharing a derived ``resource_key`` contend through one gate.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._gates: dict[tuple[int, str, int], _AdmissionGate] = {}
        self._clock = clock or time.monotonic

    def _gate(self, key_name: str, max_concurrent: int) -> _AdmissionGate:
        key = (id(asyncio.get_running_loop()), key_name, max_concurrent)
        gate = self._gates.get(key)
        if gate is None:
            gate = _AdmissionGate(capacity=max_concurrent, _clock=self._clock)
            self._gates[key] = gate
        return gate

    def has_available_capacity(
        self,
        *,
        route_name: str,
        max_concurrent: int | None,
        resource_key: str | None = None,
    ) -> bool:
        """Return a best-effort immediate-capacity view for selection.

        It is deliberately only a hint: ``acquire`` is authoritative. Queued
        work makes the resource ineligible for an immediate balance decision,
        preventing a new candidate from bypassing an already waiting route.
        """
        if max_concurrent is None:
            return True
        return self._gate(resource_key or route_name, max_concurrent).has_available_capacity

    def snapshot(
        self,
        *,
        route_name: str,
        max_concurrent: int | None,
        resource_key: str | None = None,
    ) -> dict[str, int | None]:
        """Return aggregate capacity state for a route's backend resource."""
        if max_concurrent is None:
            return {"capacity": None, "active": 0, "queued": 0}
        return self._gate(resource_key or route_name, max_concurrent).snapshot()

    async def acquire(
        self,
        *,
        req_id: str,
        route_name: str,
        max_concurrent: int | None,
        queue_timeout: float | None,
        routing_priority: int = 0,
        queue_aging: Any = None,
        deadline_policy: Any = None,
        first_token_target_at: float | None = None,
        estimated_ttft_ms: float | None = None,
        service_route_name: str | None = None,
        resource_key: str | None = None,
        dispatcher: Any = None,
    ) -> AdmissionLease | None:
        """Acquire one resource slot or reject after the route's wait budget."""
        if max_concurrent is None:
            return None
        gate = self._gate(resource_key or route_name, max_concurrent)
        timeout = max(0.0, float(queue_timeout or 0.0))
        aging = parse_queue_aging(queue_aging)
        deadline = parse_deadline_policy(deadline_policy)
        estimate_seconds = (
            float(estimated_ttft_ms) / 1000.0
            if isinstance(estimated_ttft_ms, (int, float))
            and not isinstance(estimated_ttft_ms, bool)
            and math.isfinite(float(estimated_ttft_ms))
            and float(estimated_ttft_ms) > 0
            else None
        )
        waiter = gate.acquire_or_enqueue(
            routing_priority,
            aging,
            deadline,
            first_token_target_at,
            estimate_seconds,
        )
        queued = waiter is not None
        if queued:
            events_request.emit_admission_queued(
                req_id,
                route=route_name,
                max_concurrent=max_concurrent,
                queue_timeout_ms=round(timeout * 1000.0),
                routing_priority=routing_priority,
                queue_aging_interval_ms=(round(aging.interval_seconds * 1000.0) if aging else None),
                queue_aging_priority_step=aging.priority_step if aging else None,
                queue_aging_max_boost=aging.max_boost if aging else None,
                deadline_policy_enabled=deadline.enabled,
                first_token_target_remaining_ms=(
                    round(max(0.0, first_token_target_at - self._clock()) * 1000.0)
                    if first_token_target_at is not None
                    else None
                ),
                estimated_ttft_ms=estimated_ttft_ms if estimate_seconds else None,
                service_route=service_route_name,
                dispatcher=dispatcher,
            )
            try:
                if timeout == 0.0:
                    raise TimeoutError
                await asyncio.wait_for(asyncio.shield(waiter.future), timeout=timeout)
            except TimeoutError:
                if waiter.granted:
                    gate.release()
                else:
                    gate.cancel(waiter)
                events_request.emit_admission_rejected(
                    req_id,
                    route=route_name,
                    max_concurrent=max_concurrent,
                    queue_timeout_ms=round(timeout * 1000.0),
                    routing_priority=routing_priority,
                    service_route=service_route_name,
                    dispatcher=dispatcher,
                )
                return None
            except asyncio.CancelledError:
                if waiter.granted:
                    gate.release()
                else:
                    gate.cancel(waiter)
                raise
        events_request.emit_admission_acquired(
            req_id,
            route=route_name,
            max_concurrent=max_concurrent,
            waited=queued,
            routing_priority=routing_priority,
            effective_routing_priority=(waiter.effective_priority(self._clock()) if waiter else routing_priority),
            deadline_boost=waiter.deadline_boost(self._clock()) if waiter else 0,
            deadline_policy_enabled=deadline.enabled,
            first_token_target_remaining_ms=(
                round(max(0.0, first_token_target_at - self._clock()) * 1000.0)
                if first_token_target_at is not None
                else None
            ),
            estimated_ttft_ms=estimated_ttft_ms if estimate_seconds else None,
            queue_wait_ms=(round((self._clock() - waiter.enqueued_at) * 1000.0) if waiter else 0),
            service_route=service_route_name,
            dispatcher=dispatcher,
        )
        return AdmissionLease(route_name, gate, dispatcher)


_controller = RouteAdmissionController()


def get_route_admission_controller() -> RouteAdmissionController:
    """Return the process-local controller used by the HTTP endpoints."""
    return _controller
