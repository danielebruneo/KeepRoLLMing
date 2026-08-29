"""Unit coverage for the bounded route-status dashboard registry."""

from __future__ import annotations

import json

from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.route_status import RouteStatusRegistry

NOW = 1_700_000_000.0


def _event(event_type: str, data: dict, *, req_id: str = "r-1", at: float = NOW) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        source=EventSource(domain="execution", component="test"),
        data=data,
        timestamp_ns=int(at * 1_000_000_000),
        req_id=req_id,
    )


def test_snapshot_tracks_activity_errors_and_canonical_performance() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(_event(
        "execution.chat.route_resolved", {"resolved_route": "chat/main"}, at=NOW - 10
    ))
    registry(_event(
        "execution.chat.pipeline_error", {"error": "filter failed"}, at=NOW - 5
    ))
    registry(_event(
        "execution.performance.request_complete",
        {
            "route_name": "chat/main",
            "elapsed_ms": 1_000,
            "ttft_ms": 200,
            "completion_tokens": 80,
            "prompt_tokens": 100,
            "total_tokens": 180,
            "cached_prompt_tokens": 20,
            "finish_reason": "stop",
        },
        at=NOW,
    ))

    snapshot = registry.snapshot("chat/main", now=NOW)

    assert len(snapshot["activity"]) == 60
    assert snapshot["activity"][-1]["requests"] == 1
    assert snapshot["errors"][0]["message"] == "filter failed"
    assert snapshot["errors"][0]["request_id"] == "r-1"
    assert snapshot["pending_requests"] == []
    assert snapshot["active_requests"] == []
    assert snapshot["performance"] == {
        "samples": 1,
        "avg_prompt_tps": 400.0,
        "avg_completion_tps": 100.0,
        "avg_ttft_ms": 200.0,
        "avg_elapsed_ms": 1000.0,
    }


def test_snapshot_discards_entries_outside_rolling_hour() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(_event(
        "execution.chat.route_resolved", {"resolved_route": "chat/main"}, at=NOW - 3601
    ))
    registry(_event(
        "execution.chat.route_resolved", {"resolved_route": "chat/main"}, at=NOW - 60
    ))

    snapshot = registry.snapshot("chat/main", now=NOW)

    assert sum(bucket["requests"] for bucket in snapshot["activity"]) == 1


def test_seed_uses_only_recent_jsonl_records(tmp_path) -> None:
    path = tmp_path / "chat-main.requests.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({
                "route_name": "chat/main", "req_id": "old", "completed_at": NOW - 3601,
                "elapsed_ms": 200, "ttft_ms": 100, "prompt_tps": 1,
            }),
            json.dumps({
                "route_name": "chat/main", "req_id": "recent", "completed_at": NOW - 10,
                "elapsed_ms": 500, "ttft_ms": 100, "prompt_tps": 20,
                "completion_tps": 50, "finish_reason": "error",
            }),
        ]),
        encoding="utf-8",
    )
    registry = RouteStatusRegistry(clock=lambda: NOW)

    assert registry.seed_from_performance_logs(tmp_path) == 1
    snapshot = registry.snapshot("chat/main", now=NOW)

    assert sum(bucket["requests"] for bucket in snapshot["activity"]) == 1
    assert snapshot["performance"]["avg_prompt_tps"] == 20.0
    assert snapshot["errors"][0]["request_id"] == "recent"


def test_snapshot_exposes_pending_and_active_requests() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(_event(
        "execution.chat.route_resolved", {"resolved_route": "chat/main"}, req_id="pending"
    ))
    registry(_event(
        "execution.chat.route_resolved", {"resolved_route": "chat/main"}, req_id="active"
    ))
    registry(_event(
        "execution.chat.request_route", {"route": "chat/main", "stream": True}, req_id="active"
    ))
    registry(_event(
        "execution.streaming.upstream_connected", {}, req_id="active"
    ))

    snapshot = registry.snapshot("chat/main", now=NOW + 1)

    assert snapshot["pending_requests"] == [{
        "request_id": "pending", "phase": "preparing", "stream": None,
        "started_at": snapshot["pending_requests"][0]["started_at"], "elapsed_ms": 1000.0,
    }]
    assert snapshot["active_requests"] == [{
        "request_id": "active", "phase": "streaming", "stream": True,
        "started_at": snapshot["active_requests"][0]["started_at"], "elapsed_ms": 1000.0,
    }]
