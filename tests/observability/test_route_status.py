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
    registry(_event("execution.chat.route_resolved", {"resolved_route": "chat/main"}, at=NOW - 10))
    registry(_event("execution.chat.pipeline_error", {"error": "filter failed"}, at=NOW - 5))
    registry(
        _event(
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
        )
    )

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


def test_ttft_estimate_requires_evidence_and_uses_a_conservative_quantile() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    for index, ttft_ms in enumerate((100, 200, 300, 400, 500)):
        registry(
            _event(
                "execution.performance.request_complete",
                {
                    "route_name": "chat/main",
                    "elapsed_ms": 1_000,
                    "ttft_ms": ttft_ms,
                    "completion_tokens": 10,
                    "prompt_tokens": 10,
                    "total_tokens": 20,
                },
                req_id=f"r-{index}",
                at=NOW - index,
            )
        )

    assert registry.estimated_ttft_ms("chat/main", min_samples=6) is None
    assert registry.estimated_ttft_ms("chat/main", min_samples=5) == 400.0


def test_promotion_metrics_require_multiple_complete_rate_samples() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    for index in range(3):
        registry(
            _event(
                "execution.performance.request_complete",
                {
                    "route_name": "pool/fast",
                    "elapsed_ms": 1000,
                    "ttft_ms": 100,
                    "completion_tokens": 100,
                    "prompt_tokens": 500,
                    "total_tokens": 600,
                },
                req_id=f"metric-{index}",
                at=NOW + index,
            )
        )

    metrics = registry.promotion_metrics("pool/fast")
    assert metrics is not None
    assert metrics["prompt_tps"] == 5000.0
    assert 110 < metrics["completion_tps"] < 112
    assert registry.promotion_metrics("pool/fast", min_samples=4) is None
    assert registry.promotion_metrics("pool/missing") is None


def test_snapshot_discards_entries_outside_rolling_hour() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(_event("execution.chat.route_resolved", {"resolved_route": "chat/main"}, at=NOW - 3601))
    registry(_event("execution.chat.route_resolved", {"resolved_route": "chat/main"}, at=NOW - 60))

    snapshot = registry.snapshot("chat/main", now=NOW)

    assert sum(bucket["requests"] for bucket in snapshot["activity"]) == 1


def test_snapshot_tracks_selector_activity_and_balance_evidence() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(
        _event(
            "execution.routing.balance_decision",
            {
                "requested_route": "public/code",
                "selected_route": "code/runner-a",
                "affinity_state": "cache_hit",
            },
            at=NOW - 1,
        )
    )

    snapshot = registry.snapshot("public/code", now=NOW)

    assert sum(bucket["requests"] for bucket in snapshot["activity"]) == 1
    assert snapshot["balance"] == {
        "selections": 1,
        "targets": {"code/runner-a": 1},
        "affinity": {"cache_hit": 1},
        "last_selected_route": "code/runner-a",
    }


def test_snapshot_records_selector_balance_rejection() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(
        _event(
            "execution.routing.balance_rejected",
            {
                "requested_route": "public/code",
                "candidate_states": {"code/runner": "context_insufficient"},
            },
            at=NOW - 1,
        )
    )

    snapshot = registry.snapshot("public/code", now=NOW)

    assert sum(bucket["requests"] for bucket in snapshot["activity"]) == 1
    assert snapshot["errors"] == [
        {
            "at": NOW - 1,
            "timestamp": "2023-11-14T22:13:19Z",
            "type": "execution.routing.balance_rejected",
            "request_id": "r-1",
            "message": "no eligible balance target",
            "status": 503,
        }
    ]


def test_snapshot_records_capacity_expiry_as_retryable_429() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(
        _event(
            "execution.routing.balance_rejected",
            {
                "requested_route": "public/code",
                "candidate_states": {"code/runner": "capacity_unavailable"},
                "status": 429,
            },
            at=NOW - 1,
        )
    )

    snapshot = registry.snapshot("public/code", now=NOW)

    assert snapshot["errors"][0]["status"] == 429
    assert snapshot["errors"][0]["message"] == "capacity unavailable until admission timeout"


def test_seed_uses_only_recent_jsonl_records(tmp_path) -> None:
    path = tmp_path / "chat-main.requests.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "route_name": "chat/main",
                        "req_id": "old",
                        "completed_at": NOW - 3601,
                        "elapsed_ms": 200,
                        "ttft_ms": 100,
                        "prompt_tps": 1,
                    }
                ),
                json.dumps(
                    {
                        "route_name": "chat/main",
                        "req_id": "recent",
                        "completed_at": NOW - 10,
                        "elapsed_ms": 500,
                        "ttft_ms": 100,
                        "prompt_tps": 20,
                        "completion_tps": 50,
                        "finish_reason": "error",
                    }
                ),
            ]
        ),
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
    registry(_event("execution.chat.route_resolved", {"resolved_route": "chat/main"}, req_id="pending"))
    registry(_event("execution.chat.route_resolved", {"resolved_route": "chat/main"}, req_id="active"))
    registry(_event("execution.chat.request_route", {"route": "chat/main", "stream": True}, req_id="active"))
    registry(_event("execution.streaming.upstream_connected", {}, req_id="active"))

    snapshot = registry.snapshot("chat/main", now=NOW + 1)

    assert snapshot["pending_requests"] == [
        {
            "request_id": "pending",
            "phase": "preparing",
            "stream": None,
            "started_at": snapshot["pending_requests"][0]["started_at"],
            "elapsed_ms": 1000.0,
        }
    ]
    assert snapshot["active_requests"] == [
        {
            "request_id": "active",
            "phase": "streaming",
            "stream": True,
            "started_at": snapshot["active_requests"][0]["started_at"],
            "elapsed_ms": 1000.0,
        }
    ]


def test_snapshot_exposes_queued_admission_requests() -> None:
    registry = RouteStatusRegistry(clock=lambda: NOW)
    registry(
        _event(
            "request.lifecycle.admission.queued",
            {"route": "chat/main", "max_concurrent": 1},
            req_id="queued",
        )
    )

    snapshot = registry.snapshot("chat/main", now=NOW + 1)

    assert snapshot["queued_requests"] == [
        {
            "request_id": "queued",
            "phase": "queued",
            "stream": None,
            "started_at": snapshot["queued_requests"][0]["started_at"],
            "elapsed_ms": 1000.0,
        }
    ]
