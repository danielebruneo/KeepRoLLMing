"""Regression tests for the immutable endpoint RoutePlan boundary."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from keeprollming.core.config_types import RouteSettings
from keeprollming.overrides import ALLOWED_OVERRIDE_KEYS
from keeprollming.routing import (
    BalanceConfigurationError,
    DefaultSettings,
    Route,
    RoutePlan,
    RouteRerouteError,
    resolve_balance_selection,
    resolve_reroute_destination,
)
from keeprollming.routing.balancing import (
    estimate_promotion_benefit,
    parse_balance_wait_policy,
    parse_capability_enforcement,
    parse_compaction_handoff_policy,
    parse_preserved_override_keys,
    parse_reservation_policy,
)


def _plan(*, filters=None, upstream_url="http://upstream:8000/v1"):
    route = Route(
        name="chat/main",
        pattern="chat/main",
        model="backend-model",
        upstream_url=upstream_url,
        upstream_headers={"X-Route": "main"},
        api_key="secret",
        filters=filters,
        overrides={"temperature": 0.2},
    )
    settings = RouteSettings.from_route(route, "backend-model")
    return RoutePlan.compile(
        route=route,
        client_model="chat/main",
        model="backend-model",
        settings=settings,
        context_window=32768,
        default_max_tokens=8192,
        upstream_url=upstream_url,
    )


def test_route_plan_snapshots_route_config_and_builds_endpoint_url():
    filters = {
        "timestamp": {"enabled": True, "template": "before"},
        "model_nudge": {"enabled": False},
    }
    plan = _plan(filters=filters)
    filters["timestamp"]["template"] = "after"

    assert plan.endpoint_url == "http://upstream:8000/v1/chat/completions"
    assert plan.route_name == "chat/main"
    assert plan.request_timeout == 120.0
    assert plan.summary_model == "backend-model"
    assert plan.enabled_filters == ("timestamp",)
    assert len(plan.fallback_attempts) == 1
    assert plan.fallback_attempts[0].route is plan.route
    assert plan.fallback_attempts[0].model == "backend-model"
    assert plan.fallback_attempts[0].endpoint_url == "http://upstream:8000/v1/chat/completions"
    assert plan.build_overrides() == {"temperature": 0.2}
    assert plan.filters["timestamp"]["template"] == "before"
    with pytest.raises(TypeError):
        plan.filters["new"] = {}  # type: ignore[index]


def test_route_plan_builds_request_scoped_pipeline_and_headers():
    plan = _plan(filters={"timestamp": {"enabled": True}})

    first = plan.build_pipeline()
    second = plan.build_pipeline()

    assert first is not second
    assert first._stream_filter_config == second._stream_filter_config
    assert plan.build_upstream_headers() == {
        "X-Route": "main",
        "Authorization": "Bearer secret",
    }


def test_route_plan_normalizes_base_url_without_v1_suffix():
    plan = _plan(upstream_url="http://upstream:8000")

    assert plan.endpoint_url == "http://upstream:8000/v1/chat/completions"


def test_reroute_resolves_destination_from_its_own_inheritance_tree():
    requested = Route(
        name="public/code",
        pattern="public/code",
        model="source-model",
        upstream_url="http://source:8000",
        filters={"timestamp": {"enabled": True}},
    )
    base = Route(
        name="base/runner",
        pattern="base/runner",
        model="target-model",
        upstream_url="http://target:9000",
        request_timeout=45,
        filters={"system_prompt": {"enabled": True}},
    )
    target = Route(
        name="code/runner",
        pattern="code/runner",
        extends="base/runner",
        upstream_headers={"X-Target": "yes"},
    )
    routes = {route.name: route for route in (requested, base, target)}

    result = resolve_reroute_destination(
        requested_route=requested,
        destination_name="code/runner",
        routes_by_name=routes,
        defaults=DefaultSettings(),
    )

    assert result.chain == ("public/code", "code/runner")
    assert result.requested_route_name == "public/code"
    assert result.route.name == "code/runner"
    assert result.route.model == "target-model"
    assert result.route.upstream_url == "http://target:9000"
    assert result.route.request_timeout == 45
    assert result.route.filters == {"system_prompt": {"enabled": True}}
    assert result.route.upstream_headers == {"X-Target": "yes"}


@pytest.mark.parametrize(
    ("destination", "routes", "expected"),
    [
        ("missing", {}, "not found"),
        (
            "private/runner",
            {
                "private/runner": Route(
                    name="private/runner",
                    pattern="private/runner",
                    _is_private=True,
                )
            },
            "is private",
        ),
    ],
)
def test_reroute_rejects_invalid_destinations(destination, routes, expected):
    requested = Route(name="public/code", pattern="public/code")
    with pytest.raises(RouteRerouteError, match=expected):
        resolve_reroute_destination(
            requested_route=requested,
            destination_name=destination,
            routes_by_name=routes,
        )


def test_reroute_rejects_cycles_before_resolving_destination():
    requested = Route(name="public/code", pattern="public/code")
    target = Route(name="code/runner", pattern="code/runner")
    with pytest.raises(RouteRerouteError, match="cycle"):
        resolve_reroute_destination(
            requested_route=requested,
            destination_name="public/code",
            routes_by_name={requested.name: requested, target.name: target},
            chain=("public/code", "code/runner"),
        )


def test_route_plan_preserves_requested_route_and_reroute_audit_chain():
    target = Route(
        name="code/runner",
        pattern="code/runner",
        model="runner-model",
        upstream_url="http://target:9000",
    )
    plan = RoutePlan.compile(
        route=target,
        client_model="public/code",
        model="runner-model",
        settings=RouteSettings.from_route(target, "runner-model"),
        context_window=32768,
        default_max_tokens=8192,
        upstream_url="http://target:9000",
        requested_route_name="public/code",
        reroute_chain=("public/code", "code/runner"),
    )

    assert plan.route_name == "code/runner"
    assert plan.requested_route_name == "public/code"
    assert plan.reroute_chain == ("public/code", "code/runner")


def test_balance_selects_highest_priority_concrete_target_and_keeps_alternatives():
    selector = Route(
        name="public/code",
        pattern="public/code",
        model="unused-selector-model",
        balance={
            "targets": [
                {"route": "code/runner-low", "priority": 10},
                {"route": "code/runner-high", "priority": 100},
            ]
        },
    )
    high = Route(
        name="code/runner-high",
        pattern="code/runner-high",
        model="high-model",
        upstream_url="http://high:8000",
    )
    low = Route(
        name="code/runner-low",
        pattern="code/runner-low",
        model="low-model",
        upstream_url="http://low:8000",
    )
    routes = {route.name: route for route in (selector, high, low)}

    selection = resolve_balance_selection(selector, routes_by_name=routes, defaults=DefaultSettings())

    assert selection.balanced is True
    assert selection.selected_route.name == "code/runner-high"
    assert selection.selected_model == "high-model"
    assert selection.reroute_chain == ("public/code", "code/runner-high")
    assert [candidate.route.name for candidate in selection.alternatives] == [
        "code/runner-high",
        "code/runner-low",
    ]


def test_balance_can_recurse_to_a_nested_non_private_selector():
    outer = Route(
        name="public/code",
        pattern="public/code",
        model="outer-unused",
        balance={"targets": ["pool/fast"]},
    )
    pool = Route(
        name="pool/fast",
        pattern="pool/fast",
        model="pool-unused",
        balance={"targets": ["code/runner"]},
    )
    runner = Route(
        name="code/runner",
        pattern="code/runner",
        model="runner-model",
        upstream_url="http://runner:8000",
    )
    routes = {route.name: route for route in (outer, pool, runner)}

    selection = resolve_balance_selection(outer, routes_by_name=routes)

    assert selection.selected_route.name == "code/runner"
    assert selection.reroute_chain == ("public/code", "pool/fast", "code/runner")


def test_balance_preserves_only_explicit_source_overrides():
    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-unused",
        overrides={"reasoning_effort": "xhigh", "temperature": 0.9},
        balance={
            "targets": ["code/runner"],
            "preserve_overrides": ["reasoning_effort"],
        },
    )
    runner = Route(
        name="code/runner",
        pattern="code/runner",
        model="runner-model",
        upstream_url="http://runner:8000",
        overrides={"reasoning_effort": "low", "temperature": 0.2},
    )

    selection = resolve_balance_selection(selector, routes_by_name={selector.name: selector, runner.name: runner})

    assert dict(selection.preserved_overrides) == {"reasoning_effort": "xhigh"}
    with pytest.raises(TypeError):
        selection.preserved_overrides["temperature"] = 0.9  # type: ignore[index]


def test_balance_preserves_only_explicit_source_filters():
    selector = Route(
        name="bot/batch",
        pattern="bot/batch",
        model="selector-unused",
        filters={
            "tool_rewrite": {"enabled": True, "supported_patterns": ["nested"]},
            "timestamp": {"enabled": True},
        },
        balance={"targets": ["pool/runner"], "preserve_filters": ["tool_rewrite"]},
    )
    runner = Route(
        name="pool/runner",
        pattern="pool/runner",
        model="runner-model",
        upstream_url="http://runner:8000",
        filters={"tool_rewrite": {"enabled": False}, "timestamp": {"enabled": False}},
    )

    selection = resolve_balance_selection(selector, routes_by_name={selector.name: selector, runner.name: runner})

    assert dict(selection.preserved_filters["tool_rewrite"]) == {
        "enabled": True,
        "supported_patterns": ["nested"],
    }
    assert "timestamp" not in selection.preserved_filters


@pytest.mark.parametrize("preserve_all", [True, "all"])
def test_balance_preserves_all_configured_source_filters(preserve_all):
    selector = Route(
        name="code/balanced",
        pattern="code/balanced",
        model="selector-unused",
        filters={
            "tool_rewrite": {"enabled": True, "supported_patterns": ["nested"]},
            "timestamp": {"enabled": False},
        },
        balance={"targets": ["pool/runner"], "preserve_filters": preserve_all},
    )
    runner = Route(
        name="pool/runner",
        pattern="pool/runner",
        model="runner-model",
        upstream_url="http://runner:8000",
        filters={"tool_rewrite": {"enabled": False}, "multimodal_validator": {"enabled": True}},
    )

    selection = resolve_balance_selection(selector, routes_by_name={selector.name: selector, runner.name: runner})

    assert set(selection.preserved_filters) == {"tool_rewrite", "timestamp"}
    plan = RoutePlan.compile(
        route=runner,
        client_model="code/balanced",
        model="runner-model",
        settings=RouteSettings.from_route(runner, "runner-model"),
        context_window=32768,
        default_max_tokens=8192,
        upstream_url="http://runner:8000/v1",
        preserved_filters=selection.preserved_filters,
    )
    assert plan.filters["tool_rewrite"]["enabled"] is True
    assert plan.filters["timestamp"]["enabled"] is False
    assert plan.filters["multimodal_validator"]["enabled"] is True


def test_balance_preserve_filters_false_preserves_none():
    selector = Route(
        name="code/balanced",
        pattern="code/balanced",
        model="selector-unused",
        filters={"tool_rewrite": {"enabled": True}},
        balance={"targets": ["pool/runner"], "preserve_filters": False},
    )
    runner = Route(
        name="pool/runner",
        pattern="pool/runner",
        model="runner-model",
        upstream_url="http://runner:8000",
    )

    selection = resolve_balance_selection(selector, routes_by_name={selector.name: selector, runner.name: runner})

    assert dict(selection.preserved_filters) == {}


def test_balance_rejects_preserved_filter_not_configured_on_source():
    selector = Route(
        name="bot/batch",
        pattern="bot/batch",
        model="selector-unused",
        balance={"targets": ["pool/runner"], "preserve_filters": ["tool_rewrite"]},
    )
    runner = Route(
        name="pool/runner",
        pattern="pool/runner",
        model="runner-model",
        upstream_url="http://runner:8000",
    )
    with pytest.raises(BalanceConfigurationError, match="not configured"):
        resolve_balance_selection(selector, routes_by_name={selector.name: selector, runner.name: runner})


@pytest.mark.parametrize("preserve_filters", ["sometimes", 1, {}])
def test_balance_rejects_invalid_preserve_filters_value(preserve_filters):
    selector = Route(
        name="code/balanced",
        pattern="code/balanced",
        model="selector-unused",
        filters={"tool_rewrite": {"enabled": True}},
        balance={"targets": ["pool/runner"], "preserve_filters": preserve_filters},
    )
    runner = Route(
        name="pool/runner",
        pattern="pool/runner",
        model="runner-model",
        upstream_url="http://runner:8000",
    )
    with pytest.raises(BalanceConfigurationError, match="must be a list"):
        resolve_balance_selection(selector, routes_by_name={selector.name: selector, runner.name: runner})


def test_balance_preserve_overrides_uses_the_complete_canonical_vocabulary():
    balance = {
        "targets": ["code/runner"],
        "preserve_overrides": sorted(ALLOWED_OVERRIDE_KEYS),
    }

    assert parse_preserved_override_keys(balance, route_name="public/code") == tuple(sorted(ALLOWED_OVERRIDE_KEYS))


@pytest.mark.parametrize(
    ("balance", "routes", "expected"),
    [
        ({"targets": ["missing"]}, (), "not found"),
        (
            {"targets": ["private/runner"]},
            (Route(name="private/runner", pattern="private/runner", model="x", _is_private=True),),
            "private",
        ),
        ({"targets": [{"route": "runner", "priority": "high"}]}, (), "priority"),
        (
            {"targets": ["runner"], "preserve_overrides": ["not_an_override"]},
            (),
            "unsupported preserved override",
        ),
        (
            {"targets": [], "preserve_overrides": ["reasoning_effort"]},
            (),
            "requires non-empty",
        ),
    ],
)
def test_balance_rejects_invalid_targets(balance, routes, expected):
    selector = Route(name="public/code", pattern="public/code", model="unused", balance=balance)
    routes_by_name = {route.name: route for route in (selector, *routes)}
    with pytest.raises(BalanceConfigurationError, match=expected):
        resolve_balance_selection(selector, routes_by_name=routes_by_name)


def test_balance_reservation_policy_defaults_and_validation():
    policy = parse_reservation_policy(
        {"targets": ["runner"], "idle_reservation": {"min_seconds": 30, "max_seconds": 120}},
        route_name="public/code",
    )

    assert policy.affinity_ttl_seconds == 3600
    assert policy.enabled is True
    assert policy.mode == "priority"
    assert policy.timeout_seconds is None
    assert policy.min_seconds == 30
    assert policy.max_seconds == 120
    assert policy.promote_when_beneficial is True

    disabled_promotion = parse_reservation_policy(
        {"targets": ["runner"], "promote_when_beneficial": False},
        route_name="public/code",
    )
    assert disabled_promotion.promote_when_beneficial is False

    session_policy = parse_reservation_policy(
        {
            "targets": ["runner"],
            "idle_reservation": {
                "mode": "session",
                "timeout_seconds": 900,
            },
        },
        route_name="public/code",
    )
    assert session_policy.mode == "session"
    assert session_policy.timeout_seconds == 900

    threshold_policy = parse_reservation_policy(
        {
            "targets": ["runner"],
            "idle_reservation": {
                "min_prompt_tokens": 4096,
                "min_cached_prompt_tokens": 2048,
            },
        },
        route_name="public/code",
    )
    assert threshold_policy.min_prompt_tokens == 4096
    assert threshold_policy.min_cached_prompt_tokens == 2048

    with pytest.raises(BalanceConfigurationError, match="context ratios"):
        parse_reservation_policy(
            {
                "targets": ["runner"],
                "idle_reservation": {"min_context_ratio": 0.8, "max_context_ratio": 0.2},
            },
            route_name="public/code",
        )
    with pytest.raises(BalanceConfigurationError, match="must be 'priority' or 'session'"):
        parse_reservation_policy(
            {"targets": ["runner"], "idle_reservation": {"mode": "exclusive"}},
            route_name="public/code",
        )
    with pytest.raises(BalanceConfigurationError, match="non-negative integer"):
        parse_reservation_policy(
            {"targets": ["runner"], "idle_reservation": {"min_prompt_tokens": 1.5}},
            route_name="public/code",
        )
    with pytest.raises(BalanceConfigurationError, match="promote_when_beneficial.*boolean"):
        parse_reservation_policy(
            {"targets": ["runner"], "promote_when_beneficial": "yes"},
            route_name="public/code",
        )


def test_compaction_handoff_policy_defaults_and_validation():
    defaults = parse_compaction_handoff_policy({}, route_name="code/balanced")
    assert defaults.enabled is True
    assert defaults.max_age_seconds == 60
    assert defaults.min_context_ratio == 0.80
    assert defaults.min_completion_tokens == 5_000
    assert defaults.min_prompt_reduction_ratio == 0.50

    configured = parse_compaction_handoff_policy(
        {
            "compaction_handoff": {
                "enabled": False,
                "max_age_seconds": 30,
                "min_context_ratio": 0.85,
                "min_completion_tokens": 8_000,
                "min_prompt_reduction_ratio": 0.65,
            }
        },
        route_name="code/balanced",
    )
    assert configured.enabled is False
    assert configured.max_age_seconds == 30
    assert configured.min_context_ratio == 0.85
    assert configured.min_completion_tokens == 8_000
    assert configured.min_prompt_reduction_ratio == 0.65

    with pytest.raises(BalanceConfigurationError, match="min_context_ratio"):
        parse_compaction_handoff_policy({"compaction_handoff": {"min_context_ratio": 1.1}}, route_name="code/balanced")
    with pytest.raises(BalanceConfigurationError, match="min_completion_tokens"):
        parse_compaction_handoff_policy(
            {"compaction_handoff": {"min_completion_tokens": -1}}, route_name="code/balanced"
        )


def test_balance_promotion_estimate_accounts_for_cold_prefill_and_cache_hits():
    rates = {"prompt_tps": 500, "completion_tps": 20}
    faster = {"prompt_tps": 1000, "completion_tps": 100}

    small = estimate_promotion_benefit(
        prompt_tokens=500,
        cached_prompt_tokens=0,
        completion_tokens=1000,
        current_metrics=rates,
        candidate_metrics=faster,
    )
    assert small["decision"] == "promote"
    assert small["estimated_candidate_seconds"] < small["estimated_current_seconds"]

    large_cached_context = estimate_promotion_benefit(
        prompt_tokens=100_000,
        cached_prompt_tokens=90_000,
        completion_tokens=500,
        current_metrics=rates,
        candidate_metrics=faster,
    )
    assert large_cached_context["decision"] == "stay_pinned"
    assert large_cached_context["estimated_prefill_cost_seconds"] > 0

    assert (
        estimate_promotion_benefit(
            prompt_tokens=500,
            cached_prompt_tokens=None,
            completion_tokens=1000,
            current_metrics=None,
            candidate_metrics=faster,
        )["decision"]
        == "insufficient_metrics"
    )


def test_balance_capability_enforcement_defaults_to_false_and_validates_type():
    assert (
        parse_capability_enforcement(
            {"targets": ["runner"]},
            route_name="public/code",
        )
        is False
    )
    assert (
        parse_capability_enforcement(
            {"targets": ["runner"], "enforce_capabilities": True},
            route_name="public/code",
        )
        is True
    )
    with pytest.raises(BalanceConfigurationError, match="must be boolean"):
        parse_capability_enforcement(
            {"targets": ["runner"], "enforce_capabilities": "yes"},
            route_name="public/code",
        )


def test_balance_wait_policy_defaults_and_validates_values():
    policy = parse_balance_wait_policy({"targets": ["runner"]}, route_name="public/code")
    assert policy.retry_interval_seconds == 0.25
    assert policy.allow_reservation_bypass is True

    policy = parse_balance_wait_policy(
        {
            "targets": ["runner"],
            "selection_retry_interval_ms": 100,
            "allow_reservation_bypass": False,
        },
        route_name="public/code",
    )
    assert policy.retry_interval_seconds == 0.1
    assert policy.allow_reservation_bypass is False

    with pytest.raises(BalanceConfigurationError, match="must be positive"):
        parse_balance_wait_policy(
            {"targets": ["runner"], "selection_retry_interval_ms": 0},
            route_name="public/code",
        )
    with pytest.raises(BalanceConfigurationError, match="must be boolean"):
        parse_balance_wait_policy(
            {"targets": ["runner"], "allow_reservation_bypass": "yes"},
            route_name="public/code",
        )


def test_balance_rejects_nested_cycles():
    first = Route(
        name="first",
        pattern="first",
        model="unused",
        balance={"targets": ["second"]},
    )
    second = Route(
        name="second",
        pattern="second",
        model="unused",
        balance={"targets": ["first"]},
    )
    with pytest.raises(BalanceConfigurationError, match="cycle"):
        resolve_balance_selection(first, routes_by_name={first.name: first, second.name: second})


def test_balance_compiles_all_alternatives_before_selector_fallback():
    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-model",
        fallback_chain=["selector/fallback"],
        balance={"targets": ["runner/a", "runner/b"]},
    )
    runner_a = Route(
        name="runner/a",
        pattern="runner/a",
        model="model-a",
        upstream_url="http://a",
        fallback_chain=["runner/a-fallback"],
    )
    runner_a_fallback = Route(
        name="runner/a-fallback",
        pattern="runner/a-fallback",
        model="model-a2",
        upstream_url="http://a2",
    )
    runner_b = Route(
        name="runner/b",
        pattern="runner/b",
        model="model-b",
        upstream_url="http://b",
    )
    selector_fallback = Route(
        name="selector/fallback",
        pattern="selector/fallback",
        model="model-sf",
        upstream_url="http://sf",
    )
    routes = {
        route.name: route
        for route in (
            selector,
            runner_a,
            runner_a_fallback,
            runner_b,
            selector_fallback,
        )
    }

    selection = resolve_balance_selection(selector, routes_by_name=routes)
    attempts = selection.compile_attempt_routes(routes_by_name=routes)

    assert [(route.name, model) for route, model in attempts] == [
        ("runner/a", "model-a"),
        ("runner/a-fallback", "model-a2"),
        ("runner/b", "model-b"),
        ("selector/fallback", "model-sf"),
    ]


def test_balance_uses_selector_fallback_when_every_target_is_ineligible():
    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-model",
        balance={"targets": ["runner/a", "runner/b"]},
        fallback_chain=["selector/fallback"],
    )
    runner_a = Route(name="runner/a", pattern="runner/a", model="model-a")
    runner_b = Route(name="runner/b", pattern="runner/b", model="model-b")
    fallback = Route(
        name="selector/fallback",
        pattern="selector/fallback",
        model="model-f",
    )
    routes = {route.name: route for route in (selector, runner_a, runner_b, fallback)}

    selection = resolve_balance_selection(
        selector,
        routes_by_name=routes,
        is_eligible=lambda route: route.name == "selector/fallback",
    )

    assert selection.selected_route.name == "selector/fallback"
    assert selection.selected_via_selector_fallback is True
    assert selection.reroute_chain == ("public/code", "selector/fallback")
    assert [
        (route.name, model)
        for route, model in selection.compile_attempt_routes(
            routes_by_name=routes,
        )
    ] == [("selector/fallback", "model-f")]


def test_route_plan_resolves_named_fallback_to_its_own_transport():
    primary = Route(
        name="chat/primary",
        pattern="chat/primary",
        model="primary-model",
        upstream_url="http://primary:8000",
        fallback_chain=["chat/secondary"],
    )
    secondary = Route(
        name="chat/secondary",
        pattern="chat/secondary",
        model="secondary-model",
        upstream_url="http://secondary:9000/v1",
        upstream_headers={"X-Fallback": "yes"},
        request_timeout=42,
    )
    plan = RoutePlan.compile(
        route=primary,
        client_model="chat/primary",
        model="primary-model",
        settings=RouteSettings.from_route(primary, "primary-model"),
        context_window=32768,
        default_max_tokens=8192,
        upstream_url="http://primary:8000",
        routes_by_name={primary.name: primary, secondary.name: secondary},
        defaults=DefaultSettings(),
    )

    assert [(attempt.route_name, attempt.model, attempt.endpoint_url) for attempt in plan.fallback_attempts] == [
        ("chat/primary", "primary-model", "http://primary:8000/v1/chat/completions"),
        ("chat/secondary", "secondary-model", "http://secondary:9000/v1/chat/completions"),
    ]
    assert dict(plan.fallback_attempts[1].upstream_headers) == {"X-Fallback": "yes"}
    assert plan.fallback_attempts[1].request_timeout == 42


def test_unspecified_child_fallback_chain_inherits_parent_chain():
    from keeprollming.config import load_user_routes
    from keeprollming.routing import resolve_inherited_route

    routes = load_user_routes(
        {
            "routes": {
                "parent": {"fallback_chain": ["secondary"], "model": "primary-model"},
                "child": {"extends": "parent"},
                "secondary": {"model": "secondary-model", "upstream_url": "http://secondary"},
            }
        }
    )
    by_name = {route.name: route for route in routes}
    resolved = resolve_inherited_route(by_name["child"], by_name, defaults=DefaultSettings())

    assert resolved.fallback_chain == ["secondary"]


def test_non_streaming_transport_failure_uses_fallback_endpoint():
    from keeprollming.endpoints.chat_completions import process_non_streaming_request

    async def run():
        primary = Route(
            name="primary",
            pattern="primary",
            model="primary-model",
            fallback_chain=["secondary"],
            filters={"tool_rewrite": {"enabled": True}},
        )
        secondary = Route(
            name="secondary",
            pattern="secondary",
            model="secondary-model",
            upstream_url="http://secondary:9000",
        )
        plan = RoutePlan.compile(
            route=primary,
            client_model="primary",
            model="primary-model",
            settings=RouteSettings.from_route(primary, "primary-model"),
            context_window=1024,
            default_max_tokens=128,
            upstream_url="http://primary:8000",
            routes_by_name={primary.name: primary, secondary.name: secondary},
            defaults=DefaultSettings(),
        )

        calls = []

        class Client:
            async def post(self, target_url, **_kwargs):
                calls.append(target_url)
                if "primary" in target_url:
                    raise httpx.ConnectError("primary refused")
                return httpx.Response(
                    200,
                    json={
                        "model": "secondary-model",
                        "choices": [
                            {
                                "message": {
                                    "content": (
                                        "<tool_call><function=read_file>"
                                        "<parameter=path>README.md</parameter>"
                                        "</function></tool_call>"
                                    )
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                )

        admitted_routes = []

        async def admit(route):
            admitted_routes.append(route.name)
            return True

        with patch("keeprollming.endpoints.chat_completions._exec.emit_fallback") as emit_fallback:
            response = await process_non_streaming_request(
                url="http://primary:8000/v1/chat/completions",
                client=Client(),
                payload={"model": "primary-model", "messages": []},
                route_headers={},
                req_id="fallback-non-stream",
                upstream_model="primary-model",
                fallback_attempts=plan.fallback_attempts,
                t_start=0.0,
                route_name="primary",
                route=primary,
                request_timeout=10,
                on_fallback_attempt=admit,
            )
        assert calls == [
            "http://primary:8000/v1/chat/completions",
            "http://secondary:9000/v1/chat/completions",
        ]
        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["choices"][0]["message"]["content"] == ""
        assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "read_file"
        assert emit_fallback.call_count == 1
        assert admitted_routes == ["secondary"]
        _req_id, source, target = emit_fallback.call_args.args
        assert source["route"] == "primary"
        assert source["model"] == "primary-model"
        assert target.route_name == "secondary"
        assert emit_fallback.call_args.kwargs == {
            "reason": "transport_error",
            "attempt": 1,
            "total_attempts": 2,
            "error_type": "ConnectError",
            "error": "primary refused",
        }

    asyncio.run(run())


def test_non_streaming_client_error_is_not_retried():
    from keeprollming.endpoints.chat_completions import process_non_streaming_request

    async def run():
        primary = Route(
            name="primary",
            pattern="primary",
            model="primary-model",
            fallback_chain=["secondary"],
        )
        secondary = Route(
            name="secondary",
            pattern="secondary",
            model="secondary-model",
            upstream_url="http://secondary:9000",
        )
        plan = RoutePlan.compile(
            route=primary,
            client_model="primary",
            model="primary-model",
            settings=RouteSettings.from_route(primary, "primary-model"),
            context_window=1024,
            default_max_tokens=128,
            upstream_url="http://primary:8000",
            routes_by_name={primary.name: primary, secondary.name: secondary},
            defaults=DefaultSettings(),
        )
        calls = []
        error = {
            "error": {
                "message": "maximum context length exceeded",
                "type": "BadRequestError",
                "param": "input_tokens",
                "code": 400,
            }
        }

        class Client:
            async def post(self, target_url, **_kwargs):
                calls.append(target_url)
                return httpx.Response(400, json=error)

        with patch("keeprollming.endpoints.chat_completions._exec.emit_fallback") as emit_fallback:
            response = await process_non_streaming_request(
                url="http://primary:8000/v1/chat/completions",
                client=Client(),
                payload={"model": "primary-model", "messages": []},
                route_headers={},
                req_id="client-error-non-stream",
                upstream_model="primary-model",
                fallback_attempts=plan.fallback_attempts,
                t_start=0.0,
                route_name="primary",
                route=primary,
                request_timeout=10,
            )

        assert calls == ["http://primary:8000/v1/chat/completions"]
        assert response.status_code == 400
        assert json.loads(response.body) == error
        assert emit_fallback.call_count == 0

    asyncio.run(run())


def test_cuda_runtime_400_is_retried_through_fallback():
    from keeprollming.endpoints.chat_completions import process_non_streaming_request

    async def run():
        primary = Route(
            name="primary",
            pattern="primary",
            model="primary-model",
            fallback_chain=["secondary"],
        )
        secondary = Route(
            name="secondary",
            pattern="secondary",
            model="secondary-model",
            upstream_url="http://secondary:9000",
        )
        plan = RoutePlan.compile(
            route=primary,
            client_model="primary",
            model="primary-model",
            settings=RouteSettings.from_route(primary, "primary-model"),
            context_window=1024,
            default_max_tokens=128,
            upstream_url="http://primary:8000",
            routes_by_name={primary.name: primary, secondary.name: secondary},
            defaults=DefaultSettings(),
        )
        calls = []

        class Client:
            async def post(self, target_url, **_kwargs):
                calls.append(target_url)
                if "primary" in target_url:
                    return httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": "cudaMemGetInfo: unspecified launch failure",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "model": "secondary-model",
                        "choices": [
                            {"message": {"role": "assistant", "content": "recovered"}, "finish_reason": "stop"}
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    },
                )

        with patch("keeprollming.endpoints.chat_completions._exec.emit_fallback") as emit_fallback:
            response = await process_non_streaming_request(
                url="http://primary:8000/v1/chat/completions",
                client=Client(),
                payload={"model": "primary-model", "messages": []},
                route_headers={},
                req_id="cuda-error-fallback",
                upstream_model="primary-model",
                fallback_attempts=plan.fallback_attempts,
                t_start=0.0,
                route_name="primary",
                route=primary,
                request_timeout=10,
            )

        assert calls == [
            "http://primary:8000/v1/chat/completions",
            "http://secondary:9000/v1/chat/completions",
        ]
        assert response.status_code == 200
        assert json.loads(response.body)["choices"][0]["message"]["content"] == "recovered"
        assert emit_fallback.call_count == 1
        assert emit_fallback.call_args.kwargs["reason"] == "http_status"
        assert emit_fallback.call_args.kwargs["status"] == 400

    asyncio.run(run())


def test_cuda_runtime_400_classification_is_shared_with_streaming_path():
    from keeprollming.endpoints.streaming_handlers import _is_terminal_client_http_error

    assert not _is_terminal_client_http_error(400, "cudaMemGetInfo: unspecified launch failure")
    assert not _is_terminal_client_http_error(400, '{"error":"CUDA error: device is lost"}')
    assert _is_terminal_client_http_error(400, "maximum context length exceeded")
    assert not _is_terminal_client_http_error(503, "unavailable")
