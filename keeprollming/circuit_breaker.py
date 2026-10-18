"""Process-local, observable circuit breakers for upstream routes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .observability.events import EventSource, RuntimeEvent


@dataclass
class CircuitAttempt:
    key: str
    enabled: bool


@dataclass
class _CircuitState:
    failures: int = 0
    state: str = "closed"
    opened_at: float | None = None
    half_open_probe: bool = False


class CircuitBreakerRegistry:
    """CLOSED/OPEN/HALF_OPEN breaker state, bounded by configured routes."""

    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._states: dict[str, _CircuitState] = {}

    def allow(
        self,
        *,
        route_name: str,
        enabled: bool,
        recovery_timeout: float,
        req_id: str,
        dispatcher: Any = None,
        resource_key: str | None = None,
    ) -> CircuitAttempt | None:
        if not enabled:
            return CircuitAttempt(route_name, False)
        state_key = resource_key or route_name
        state = self._states.setdefault(state_key, _CircuitState())
        now = self._clock()
        if state.state == "open":
            assert state.opened_at is not None
            if now - state.opened_at < recovery_timeout:
                self._emit("rejected", req_id, route_name, dispatcher, state=state.state)
                return None
            state.state = "half_open"
            state.half_open_probe = False
            self._emit("half_open", req_id, route_name, dispatcher, state=state.state)
        if state.state == "half_open":
            if state.half_open_probe:
                self._emit("rejected", req_id, route_name, dispatcher, state=state.state)
                return None
            state.half_open_probe = True
        self._emit("allowed", req_id, route_name, dispatcher, state=state.state)
        return CircuitAttempt(state_key, True)

    def is_available(
        self,
        *,
        route_name: str,
        enabled: bool,
        recovery_timeout: float,
        resource_key: str | None = None,
    ) -> bool:
        """Return whether a route may be selected without consuming a probe.

        Balance selection must not call :meth:`allow`, because inspecting a
        half-open route would incorrectly reserve its sole recovery probe.
        The authoritative call remains ``allow`` after target selection.
        """
        if not enabled:
            return True
        state = self._states.get(resource_key or route_name)
        if state is None or state.state == "closed":
            return True
        if state.state == "half_open":
            return not state.half_open_probe
        assert state.opened_at is not None
        return self._clock() - state.opened_at >= recovery_timeout

    def record_success(self, attempt: CircuitAttempt | None, *, req_id: str, dispatcher: Any = None) -> None:
        if attempt is None or not attempt.enabled:
            return
        state = self._states.setdefault(attempt.key, _CircuitState())
        was_open = state.state != "closed" or state.failures
        state.failures = 0
        state.state = "closed"
        state.opened_at = None
        state.half_open_probe = False
        if was_open:
            self._emit("closed", req_id, attempt.key, dispatcher, state=state.state)

    def record_failure(
        self,
        attempt: CircuitAttempt | None,
        *,
        req_id: str,
        failure_threshold: int,
        dispatcher: Any = None,
    ) -> None:
        if attempt is None or not attempt.enabled:
            return
        state = self._states.setdefault(attempt.key, _CircuitState())
        state.half_open_probe = False
        state.failures += 1
        if state.state == "half_open" or state.failures >= failure_threshold:
            state.state = "open"
            state.opened_at = self._clock()
            self._emit(
                "opened",
                req_id,
                attempt.key,
                dispatcher,
                state=state.state,
                failures=state.failures,
            )
        else:
            self._emit(
                "failure",
                req_id,
                attempt.key,
                dispatcher,
                state=state.state,
                failures=state.failures,
            )

    def snapshot(
        self,
        route_name: str,
        *,
        resource_key: str | None = None,
    ) -> dict[str, Any]:
        """Return breaker state for a route or its shared backend resource."""
        state = self._states.get(resource_key or route_name, _CircuitState())
        return {"state": state.state, "consecutive_failures": state.failures}

    @staticmethod
    def _emit(kind: str, req_id: str, route: str, dispatcher: Any, **data: Any) -> None:
        if dispatcher is not None:
            dispatcher.emit(
                RuntimeEvent(
                    type=f"execution.circuit.{kind}",
                    timestamp_ns=time.time_ns(),
                    source=EventSource(domain="execution", component="circuit_breaker"),
                    data={"route": route, **data},
                    req_id=req_id,
                    level="WARN" if kind in {"opened", "rejected", "failure"} else "INFO",
                )
            )


_registry = CircuitBreakerRegistry()


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    return _registry
