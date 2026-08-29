"""Bounded, event-fed status window for the private ``GET /routes`` API.

The dashboard endpoint must be cheap enough to poll frequently.  This module
therefore receives the existing runtime event stream and keeps only the last
hour of route activity, terminal performance samples, and failures in memory.
It does not perform disk I/O from a request/streaming path.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..performance import compute_request_performance
from .events import RuntimeEvent

WINDOW_SECONDS = 60 * 60
WINDOW_MINUTES = 60
MAX_ERRORS_PER_ROUTE = 256
MAX_SAMPLES_PER_ROUTE = 1024

_ERROR_EVENTS = {
    "execution.chat.missing_upstream",
    "execution.chat.invalid_upstream",
    "execution.chat.upstream_error",
    "execution.chat.pipeline_error",
    "execution.chat.timeout",
    "execution.chat.failed",
    "execution.streaming.handler_error",
}

_TERMINAL_EVENTS = _ERROR_EVENTS | {
    "execution.streaming.downstream_closed",
    "request.lifecycle.cancelled",
}


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _utc_minute(minute_epoch: int) -> str:
    return datetime.fromtimestamp(minute_epoch * 60, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


class RouteStatusRegistry:
    """Maintain a one-hour, bounded operational view keyed by route name."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._activity: dict[str, dict[int, int]] = defaultdict(dict)
        self._samples: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=MAX_SAMPLES_PER_ROUTE)
        )
        self._errors: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=MAX_ERRORS_PER_ROUTE)
        )
        self._request_routes: dict[str, tuple[str, float]] = {}
        self._in_flight: dict[str, dict[str, Any]] = {}

    def __call__(self, event: RuntimeEvent) -> None:
        """Consume a runtime event; unmatched event types are intentionally ignored."""
        now = event.timestamp_ns / 1_000_000_000
        with self._lock:
            if event.type == "execution.chat.route_resolved":
                route_name = self._text(event.data.get("resolved_route"))
                if route_name:
                    self._record_activity(route_name, now)
                    if event.req_id:
                        self._request_routes[event.req_id] = (route_name, now)
                        self._in_flight[event.req_id] = {
                            "route_name": route_name,
                            "started_at": now,
                            "phase": "preparing",
                            "stream": None,
                        }
            elif event.type == "execution.chat.request_route":
                self._mark_request_started(event, now)
            elif event.type == "execution.streaming.upstream_connect":
                self._mark_in_flight(event.req_id, phase="connecting")
            elif event.type == "execution.streaming.upstream_connected":
                self._mark_in_flight(event.req_id, phase="streaming")
            elif event.type == "execution.performance.request_complete":
                route_name = self._text(event.data.get("route_name"))
                if route_name:
                    self._record_sample(route_name, event.data, now)
                    if event.data.get("finish_reason") == "error":
                        self._record_error(
                            route_name,
                            now,
                            event.req_id,
                            event_type="execution.performance.request_complete",
                            message="request completed with finish_reason=error",
                        )
                self._in_flight.pop(event.req_id or "", None)
            elif event.type in _ERROR_EVENTS:
                route_name = self._event_route(event)
                if route_name:
                    self._record_error(
                        route_name,
                        now,
                        event.req_id,
                        event_type=event.type,
                        message=self._error_message(event.data),
                        status=event.data.get("status"),
                    )
                self._in_flight.pop(event.req_id or "", None)
            elif event.type in _TERMINAL_EVENTS:
                self._in_flight.pop(event.req_id or "", None)
            self._prune(now)

    def seed_from_performance_logs(self, directory: str | Path) -> int:
        """Seed completed traffic/performance from recent JSONL records.

        Startup seeding is deliberately bounded to the same 60-minute window.
        It supplies completed-request activity until new route-resolution events
        arrive; in-flight requests before a restart cannot be reconstructed.
        Malformed lines are ignored so an old log never prevents startup.
        """
        cutoff = self._clock() - WINDOW_SECONDS
        count = 0
        base_dir = Path(directory)
        if not base_dir.is_dir():
            return count
        with self._lock:
            for path in sorted(base_dir.glob("*.requests.jsonl")):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                for line in lines:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    completed_at = _finite_number(record.get("completed_at"))
                    route_name = self._text(record.get("route_name"))
                    if completed_at is None or completed_at < cutoff or not route_name:
                        continue
                    self._record_activity(route_name, completed_at)
                    self._record_sample(route_name, record, completed_at, precomputed=True)
                    if record.get("finish_reason") == "error":
                        self._record_error(
                            route_name,
                            completed_at,
                            self._text(record.get("req_id")),
                            event_type="performance_log",
                            message="request completed with finish_reason=error",
                        )
                    count += 1
            self._prune(self._clock())
        return count

    def snapshot(self, route_name: str, *, now: float | None = None) -> dict[str, Any]:
        """Return a stable, dashboard-oriented snapshot for one route."""
        current = self._clock() if now is None else now
        with self._lock:
            self._prune(current)
            current_minute = int(current // 60)
            buckets = self._activity.get(route_name, {})
            activity = [
                {
                    "minute": _utc_minute(minute),
                    "requests": buckets.get(minute, 0),
                }
                for minute in range(current_minute - (WINDOW_MINUTES - 1), current_minute + 1)
            ]
            samples = list(self._samples.get(route_name, ()))
            errors = [
                error for error in self._errors.get(route_name, ())
                if error["at"] >= current - WINDOW_SECONDS
            ]
            return {
                "activity": activity,
                "errors": list(reversed(errors)),
                "pending_requests": self._requests_for_route(
                    route_name, current, phases={"preparing", "connecting"}
                ),
                "active_requests": self._requests_for_route(
                    route_name, current, phases={"requesting", "streaming"}
                ),
                "performance": {
                    "samples": len(samples),
                    "avg_prompt_tps": self._average(samples, "prompt_tps"),
                    "avg_completion_tps": self._average(samples, "completion_tps"),
                    "avg_ttft_ms": self._average(samples, "ttft_ms"),
                    "avg_elapsed_ms": self._average(samples, "elapsed_ms"),
                },
            }

    @staticmethod
    def _text(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _event_route(self, event: RuntimeEvent) -> str | None:
        route = self._text(event.data.get("route")) or self._text(event.data.get("route_name"))
        if route:
            return route
        if event.req_id and event.req_id in self._request_routes:
            return self._request_routes[event.req_id][0]
        return None

    def _mark_request_started(self, event: RuntimeEvent, now: float) -> None:
        if not event.req_id:
            return
        request = self._in_flight.get(event.req_id)
        if request is None:
            return
        request["stream"] = bool(event.data.get("stream"))
        if not request["stream"]:
            request["phase"] = "requesting"
        elif request["phase"] == "preparing":
            request["phase"] = "connecting"

    def _mark_in_flight(self, req_id: str | None, *, phase: str) -> None:
        if not req_id:
            return
        request = self._in_flight.get(req_id)
        if request is not None:
            request["phase"] = phase

    def _requests_for_route(
        self, route_name: str, now: float, *, phases: set[str]
    ) -> list[dict[str, Any]]:
        requests = []
        for req_id, request in self._in_flight.items():
            if request["route_name"] != route_name or request["phase"] not in phases:
                continue
            started_at = request["started_at"]
            requests.append({
                "request_id": req_id,
                "phase": request["phase"],
                "stream": request["stream"],
                "started_at": datetime.fromtimestamp(
                    started_at, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "elapsed_ms": round((now - started_at) * 1000, 1),
            })
        return sorted(requests, key=lambda item: item["started_at"])

    def _record_activity(self, route_name: str, at: float) -> None:
        minute = int(at // 60)
        buckets = self._activity[route_name]
        buckets[minute] = buckets.get(minute, 0) + 1

    def _record_sample(
        self, route_name: str, data: dict[str, Any], at: float, *, precomputed: bool = False
    ) -> None:
        metrics = data if precomputed else compute_request_performance(
            elapsed_ms=data.get("elapsed_ms"),
            completion_tokens=data.get("completion_tokens"),
            ttft_ms=data.get("ttft_ms"),
            prompt_tokens=data.get("prompt_tokens"),
            total_tokens=data.get("total_tokens"),
            cached_prompt_tokens=data.get("cached_prompt_tokens"),
        )
        self._samples[route_name].append({
            "at": at,
            "prompt_tps": _finite_number(metrics.get("prompt_tps")),
            "completion_tps": _finite_number(metrics.get("completion_tps")),
            "ttft_ms": _finite_number(metrics.get("ttft_ms")),
            "elapsed_ms": _finite_number(metrics.get("elapsed_ms")),
        })

    def _record_error(
        self,
        route_name: str,
        at: float,
        req_id: str | None,
        *,
        event_type: str,
        message: str | None,
        status: Any = None,
    ) -> None:
        error: dict[str, Any] = {
            "at": at,
            "timestamp": datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": event_type,
            "request_id": req_id,
            "message": message,
        }
        if status is not None:
            error["status"] = status
        self._errors[route_name].append(error)

    @staticmethod
    def _error_message(data: dict[str, Any]) -> str | None:
        for key in ("error", "body", "message"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value[:4096]
        return None

    @staticmethod
    def _average(samples: list[dict[str, Any]], key: str) -> float | None:
        values = [sample[key] for sample in samples if sample.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else None

    def _prune(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        for route_name, buckets in list(self._activity.items()):
            cutoff_minute = int(cutoff // 60)
            for minute in tuple(buckets):
                if minute < cutoff_minute:
                    del buckets[minute]
            if not buckets:
                self._activity.pop(route_name, None)
        for route_name, samples in list(self._samples.items()):
            while samples and samples[0]["at"] < cutoff:
                samples.popleft()
            if not samples:
                self._samples.pop(route_name, None)
        for route_name, errors in list(self._errors.items()):
            while errors and errors[0]["at"] < cutoff:
                errors.popleft()
            if not errors:
                self._errors.pop(route_name, None)
        for req_id, (_, at) in tuple(self._request_routes.items()):
            if at < cutoff:
                del self._request_routes[req_id]
        for req_id, request in tuple(self._in_flight.items()):
            if request["started_at"] < cutoff:
                del self._in_flight[req_id]
