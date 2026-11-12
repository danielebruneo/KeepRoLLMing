"""Deterministic state-machine tests for route circuit breakers."""

from keeprollming.circuit_breaker import CircuitBreakerRegistry


def test_local_rejection_releases_only_its_own_half_open_probe():
    clock = [0.0]
    registry = CircuitBreakerRegistry(clock=lambda: clock[0])
    attempt = registry.allow(route_name="backend", enabled=True, recovery_timeout=1, req_id="failed")
    registry.record_failure(attempt, req_id="failed", failure_threshold=1)
    clock[0] = 2.0
    probe = registry.allow(route_name="backend", enabled=True, recovery_timeout=1, req_id="probe")
    registry.abandon_attempt(probe, req_id="other-request")
    assert not registry.is_available(route_name="backend", enabled=True, recovery_timeout=1)
    registry.abandon_attempt(probe, req_id="probe")
    assert registry.is_available(route_name="backend", enabled=True, recovery_timeout=1)
    assert registry.snapshot("backend") == {"state": "half_open", "consecutive_failures": 1}
    assert registry.allow(route_name="backend", enabled=True, recovery_timeout=1, req_id="next") is not None


def test_abandonment_does_not_change_closed_or_disabled_circuit_health():
    registry = CircuitBreakerRegistry()
    attempt = registry.allow(route_name="backend", enabled=True, recovery_timeout=1, req_id="request")
    registry.abandon_attempt(attempt, req_id="request")
    registry.abandon_attempt(None, req_id="request")
    assert registry.snapshot("backend") == {"state": "closed", "consecutive_failures": 0}


def test_breaker_opens_then_recovers_through_one_half_open_probe() -> None:
    now = [0.0]
    breaker = CircuitBreakerRegistry(clock=lambda: now[0])

    first = breaker.allow(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
        req_id="one",
    )
    assert first is not None
    breaker.record_failure(first, req_id="one", failure_threshold=2)
    assert breaker.snapshot("chat/main") == {
        "state": "closed",
        "consecutive_failures": 1,
    }

    second = breaker.allow(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
        req_id="two",
    )
    assert second is not None
    breaker.record_failure(second, req_id="two", failure_threshold=2)
    assert breaker.snapshot("chat/main")["state"] == "open"
    assert (
        breaker.allow(
            route_name="chat/main",
            enabled=True,
            recovery_timeout=10,
            req_id="blocked",
        )
        is None
    )

    now[0] = 10.0
    probe = breaker.allow(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
        req_id="probe",
    )
    assert probe is not None
    assert (
        breaker.allow(
            route_name="chat/main",
            enabled=True,
            recovery_timeout=10,
            req_id="second-probe",
        )
        is None
    )
    breaker.record_success(probe, req_id="probe")
    assert breaker.snapshot("chat/main") == {
        "state": "closed",
        "consecutive_failures": 0,
    }


def test_disabled_breaker_never_retains_failure_state() -> None:
    breaker = CircuitBreakerRegistry()
    attempt = breaker.allow(
        route_name="chat/main",
        enabled=False,
        recovery_timeout=1,
        req_id="r",
    )
    assert attempt is not None
    breaker.record_failure(attempt, req_id="r", failure_threshold=1)
    assert breaker.snapshot("chat/main") == {
        "state": "closed",
        "consecutive_failures": 0,
    }


def test_availability_inspection_does_not_consume_half_open_probe() -> None:
    now = [0.0]
    breaker = CircuitBreakerRegistry(clock=lambda: now[0])
    attempt = breaker.allow(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
        req_id="open",
    )
    assert attempt is not None
    breaker.record_failure(attempt, req_id="open", failure_threshold=1)

    assert not breaker.is_available(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
    )
    now[0] = 10.0
    assert breaker.is_available(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
    )
    # The selection hint is read-only; the actual request can still claim the
    # single half-open probe.
    probe = breaker.allow(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
        req_id="probe",
    )
    assert probe is not None
    assert not breaker.is_available(
        route_name="chat/main",
        enabled=True,
        recovery_timeout=10,
    )


def test_routes_sharing_resource_key_share_circuit_state() -> None:
    breaker = CircuitBreakerRegistry()
    resource = "http://backend:8000 :: qwen"
    attempt = breaker.allow(
        route_name="chat/a",
        enabled=True,
        recovery_timeout=10,
        req_id="a",
        resource_key=resource,
    )
    assert attempt is not None
    breaker.record_failure(attempt, req_id="a", failure_threshold=1)

    assert not breaker.is_available(
        route_name="chat/b",
        enabled=True,
        recovery_timeout=10,
        resource_key=resource,
    )
    assert breaker.snapshot("chat/b", resource_key=resource) == {
        "state": "open",
        "consecutive_failures": 1,
    }
