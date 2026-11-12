"""Evidence hierarchy, resource identity, bounded persistence and confidence gates."""

from dataclasses import asdict

import pytest

from keeprollming.routing.balancing import estimate_promotion_benefit, parse_promotion_policy, validate_balance_policies
from keeprollming.routing.performance_estimates import (
    MAX_RESOURCES,
    MAX_SAMPLES,
    PromotionEstimates,
    PromotionMetricsPolicy,
    parse_metrics_policy,
    performance_identity,
    route_estimate,
)
from keeprollming.types import Route

NOW = 200000.0
KEY = performance_identity("http://backend/v1", "model-a")
HINT = {"prompt_tps": 1000, "completion_tps": 80}


def observed(count=6, age=4000, rates=(500, 50)):
    provider = PromotionEstimates()
    for i in range(count):
        provider.observe(KEY, NOW - age - count + i, *rates)
    return provider


def test_identity_normalization_and_model_isolation():
    assert KEY == performance_identity("http://backend/", "model-a")
    assert KEY != performance_identity("http://backend/", "model-b")
    assert KEY != performance_identity("http://other/", "model-a")
    assert performance_identity(None, "model-a") is None


@pytest.mark.parametrize(
    "age,source,margin", [(100, "recent", 0), (4000, "historical", 0.2), (90000, "configured", 0.35)]
)
def test_hierarchy(age, source, margin):
    result = observed(age=age).estimate(KEY, HINT, PromotionMetricsPolicy(), NOW)
    assert result["source"] == source
    assert result["margin_ratio"] == margin
    assert result["samples"] == (0 if source == "configured" else 6)
    assert result["confidence"] in {"high", "medium", "low"}


def test_insufficient_stale_variable_and_absent_evidence_fail_closed():
    policy = PromotionMetricsPolicy()
    assert observed(count=2).estimate(KEY, None, policy, NOW)["reason"] == "insufficient_samples"
    assert observed(age=90000).estimate(KEY, None, policy, NOW)["reason"] == "history_expired"
    provider = observed(count=5)
    provider.observe(KEY, NOW - 3999, 10000, 1000)
    assert provider.estimate(KEY, None, policy, NOW)["reason"] == "history_variable"
    assert provider.estimate(KEY, HINT, policy, NOW)["source"] == "configured"
    assert (
        provider.estimate(performance_identity("http://backend", "new"), None, policy, NOW)["source"] == "unavailable"
    )
    assert provider.estimate(None, HINT, policy, NOW)["reason"] == "resource_identity_unavailable"


def test_history_and_recent_are_incremental_bounded_and_pruned():
    provider = observed(count=MAX_SAMPLES + 10, age=100)
    assert provider.estimate(KEY, None, PromotionMetricsPolicy(), NOW)["samples"] == MAX_SAMPLES
    assert provider.estimate(KEY, None, PromotionMetricsPolicy(), NOW + 8 * 86400)["source"] == "unavailable"
    for i in range(MAX_RESOURCES + 1):
        provider.observe(performance_identity(f"http://node-{i}", "m"), NOW, 100, 10)
    assert len(provider._resources) == MAX_RESOURCES
    assert KEY not in provider._resources


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), -1, None, "fast"])
def test_invalid_observations_are_ignored(invalid):
    provider = PromotionEstimates()
    provider.observe(KEY, NOW, invalid, 10)
    assert provider.estimate(KEY, None, PromotionMetricsPolicy(), NOW)["source"] == "unavailable"


def test_atomic_snapshot_restores_identity_history_and_deduplicates_seed(tmp_path):
    provider = observed()
    snapshot = tmp_path / "routing_estimates.json"
    provider.save(snapshot, NOW)
    assert "http://" not in snapshot.read_text()
    restored = PromotionEstimates()
    restored.load(snapshot, NOW)
    restored.observe(KEY, NOW - 4001, 500, 50, seed=True)
    assert restored.estimate(KEY, None, PromotionMetricsPolicy(), NOW)["samples"] == 6
    assert restored.estimate(KEY, None, PromotionMetricsPolicy(), NOW)["source"] == "historical"


def test_startup_alias_records_can_arrive_out_of_order():
    provider = PromotionEstimates()
    for offset in (1, 2, 3, 4, 5, 6):
        provider.observe(KEY, NOW - offset, 100, 10, seed=True)
    assert provider.estimate(KEY, None, PromotionMetricsPolicy(), NOW)["samples"] == 6


@pytest.mark.parametrize("list_format", [False, True])
def test_hint_is_loaded_inherited_and_disabled(list_format):
    from keeprollming.config import load_user_routes
    from keeprollming.routing.router import resolve_inherited_route

    routes = {
        "parent": {"model": "m", "upstream_url": "http://node", "performance_hint": HINT},
        "child": {"extends": "parent"},
        "disabled": {"extends": "parent", "performance_hint": {"enabled": False}},
    }
    raw = [{"name": name, "pattern": name, **values} for name, values in routes.items()] if list_format else routes
    loaded = load_user_routes({"routes": raw})
    by_name = {route.name: route for route in loaded}
    child = resolve_inherited_route(by_name["child"], by_name)
    assert child.performance_hint == HINT
    disabled = resolve_inherited_route(by_name["disabled"], by_name)
    assert route_estimate(None, disabled, PromotionMetricsPolicy(), NOW)["source"] == "unavailable"
    validate_balance_policies(loaded)


def test_registry_errors_retries_and_legacy_records_never_teach_resource_estimates(tmp_path):
    from keeprollming.observability.events import EventSource, RuntimeEvent
    from keeprollming.observability.route_status import RouteStatusRegistry

    registry = RouteStatusRegistry(clock=lambda: NOW)
    route = Route(name="alias", pattern="alias", upstream_url="http://backend/v1", model="model-a")
    for i, (attempts, finish, identity) in enumerate(
        [(2, "stop", KEY), (1, "error", KEY), (1, "cancelled", KEY), (1, "stop", None)] * 3
    ):
        registry(
            RuntimeEvent(
                type="execution.performance.request_complete",
                source=EventSource(domain="execution", component="test"),
                timestamp_ns=int(NOW * 1e9),
                req_id=f"bad-{i}",
                data={
                    "route_name": "alias",
                    "elapsed_ms": 1000,
                    "ttft_ms": 100,
                    "completion_tokens": 90,
                    "prompt_tokens": 100,
                    "finish_reason": finish,
                    "upstream_attempts": attempts,
                    "backend_identity": identity,
                },
            )
        )
    assert registry.promotion_estimate(route, PromotionMetricsPolicy())["source"] == "unavailable"
    # Startup/shutdown failures are diagnosed and do not prevent serving.
    snapshot = tmp_path / "routing_estimates.json"
    snapshot.write_text("bad json")
    registry.load_promotion_estimates(snapshot)
    assert registry.promotion_estimate(route, PromotionMetricsPolicy())["source"] == "unavailable"


def test_event_to_persisted_record_to_registry_survives_restart(tmp_path, monkeypatch):
    from keeprollming.observability.consumers import PerformanceConsumer
    from keeprollming.observability.dispatcher import EventDispatcher
    from keeprollming.observability.events_execution import emit_performance_request_complete
    from keeprollming.observability.route_status import RouteStatusRegistry

    registry = RouteStatusRegistry(clock=lambda: NOW)
    dispatcher = EventDispatcher()
    consumer = PerformanceConsumer(perf_logs_dir=str(tmp_path))
    captured = []
    monkeypatch.setattr(consumer, "_append_entry", lambda path, record: captured.append(record))
    monkeypatch.setattr("keeprollming.observability.events_execution.time.time_ns", lambda: int(NOW * 1e9))
    dispatcher.subscribe("execution.performance", registry)
    dispatcher.subscribe("execution.performance", consumer)
    for i in range(6):
        emit_performance_request_complete(
            req_id=f"ok-{i}",
            model="model-a",
            route_name="alias",
            route_hierarchy=["alias"],
            stream=True,
            elapsed_ms=1000,
            ttft_ms=100,
            completion_tokens=90,
            prompt_tokens=100,
            total_tokens=190,
            finish_reason="stop",
            did_summarize=False,
            passthrough=False,
            completion_tokens_source="execution_usage",
            upstream_attempts=1,
            backend_identity=KEY,
            dispatcher=dispatcher,
        )
    assert len(captured) == 6
    assert captured[0]["backend_identity"] == KEY
    route = Route(name="new-alias", pattern="new-alias", upstream_url="http://backend", model="model-a")
    assert registry.promotion_estimate(route, PromotionMetricsPolicy())["source"] == "recent"
    snapshot = tmp_path / "routing_estimates.json"
    registry.save_promotion_estimates(snapshot)
    restarted = RouteStatusRegistry(clock=lambda: NOW + 4000)
    restarted.load_promotion_estimates(snapshot)
    assert restarted.promotion_estimate(route, PromotionMetricsPolicy())["source"] == "historical"


@pytest.mark.parametrize("stream", [False, True])
def test_handlers_emit_identity_of_effective_backend(stream, monkeypatch):
    """Exercise real producers, not only hand-built performance events."""
    import asyncio
    import time

    import httpx

    from keeprollming import app
    from keeprollming.endpoints.chat_completions import _record_final_metrics
    from keeprollming.endpoints.streaming_handlers import process_streaming_request
    from keeprollming.observability.dispatcher import EventDispatcher

    dispatcher = EventDispatcher()
    captured = []
    dispatcher.subscribe("execution.performance", captured.append)
    monkeypatch.setattr(app, "get_event_dispatcher", lambda: dispatcher)
    route = Route(name="alias", pattern="alias", upstream_url="http://backend/v1", model="model-a")
    if not stream:
        _record_final_metrics(
            {
                "model": "model-a",
                "req_id": "identity",
                "finish_reason": "stop",
                "elapsed_ms": 1000,
                "ttft_ms": 100,
                "completion_tokens": 10,
                "prompt_tokens": 100,
                "upstream_attempts": 1,
            },
            time.perf_counter() - 1,
            False,
            route.name,
            route,
        )
    else:

        async def run():
            body = (
                b'data: {"choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":100,"completion_tokens":10,"total_tokens":110}}\n\n'
                b"data: [DONE]\n\n"
            )
            transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
            async with httpx.AsyncClient(transport=transport) as client:
                chunks = [
                    chunk
                    async for chunk in process_streaming_request(
                        url="http://backend/v1/chat/completions",
                        client=client,
                        payload={
                            "model": "model-a",
                            "messages": [{"role": "user", "content": "hello"}],
                            "stream": True,
                        },
                        route_headers={},
                        route=route,
                        req_id="identity",
                        request_timeout=30,
                        fallback_attempts=[],
                        visited_models=set(),
                        upstream_model="model-a",
                        is_passthrough=False,
                        transform_reasoning_content=False,
                        add_empty_content_when_reasoning_only=False,
                        reasoning_placeholder="",
                        t_start=time.perf_counter(),
                        dispatcher=dispatcher,
                    )
                ]
            assert chunks

        asyncio.run(run())
    events = [event for event in captured if event.type == "execution.performance.request_complete"]
    assert len(events) == 1
    assert events[0].data["backend_identity"] == KEY
    assert events[0].data["finish_reason"] == "stop"


@pytest.mark.parametrize(
    "body", ["{}", "[]", '{"version":1,"resources":[]}', '{"version":1,"resources":{"x":[[1,2]]}}', "not json"]
)
def test_corrupt_snapshot_does_not_partly_replace_existing_samples(tmp_path, body):
    snapshot = tmp_path / "routing_estimates.json"
    snapshot.write_text(body)
    provider = observed()
    with pytest.raises((ValueError, TypeError)):
        provider.load(snapshot, NOW)
    assert provider.estimate(KEY, None, PromotionMetricsPolicy(), NOW)["samples"] == 6


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"typo": 3},
        {"recent_min_samples": True},
        {"history_min_samples": 0},
        {"history_min_samples": 2.5},
        {"history_max_age_seconds": 800000},
        {"configured_margin_ratio": 1},
        {"max_history_cv": float("nan")},
    ],
)
def test_metrics_validation(raw):
    with pytest.raises(ValueError):
        parse_metrics_policy(raw)


def test_metrics_tuning_and_configured_only_without_registry():
    policy = parse_promotion_policy(
        {"promotion": {"metrics": {"history_min_samples": 4, "configured_margin_ratio": 0.4}}}, route_name="selector"
    )
    assert policy.metrics.history_min_samples == 4
    assert asdict(policy)["metrics"]["configured_margin_ratio"] == 0.4
    route = Route(name="new", pattern="new", upstream_url="http://backend", model="model-a", performance_hint=HINT)
    assert route_estimate(None, route, policy.metrics, NOW)["margin_ratio"] == 0.4


@pytest.mark.parametrize(
    "hint", [{}, {"prompt_tps": 1}, {**HINT, "typo": 1}, {**HINT, "prompt_tps": -1}, {**HINT, "enabled": "no"}]
)
def test_invalid_hint_on_direct_route_rejected_at_validation(hint):
    with pytest.raises(ValueError, match="performance_hint"):
        validate_balance_policies(
            [Route(name="direct", pattern="direct", upstream_url="http://backend", model="m", performance_hint=hint)]
        )


@pytest.mark.parametrize("margin,decision", [(0, "promote"), (0.2, "stay_pinned"), (0.35, "stay_pinned")])
def test_less_reliable_evidence_requires_extra_gain(margin, decision):
    result = estimate_promotion_benefit(
        prompt_tokens=1000,
        cached_prompt_tokens=0,
        completion_tokens=100,
        current_metrics={"prompt_tps": 100, "completion_tps": 10, "source": "historical", "margin_ratio": margin},
        candidate_metrics={"prompt_tps": 110, "completion_tps": 11},
        promotion=parse_promotion_policy({"promotion": {"lookahead_turns": 0}}, route_name="s"),
    )
    assert result["decision"] == decision
    assert result["required_gain_ratio"] == margin
    assert result["current_evidence"]["source"] == "historical"
