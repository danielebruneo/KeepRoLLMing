"""Bounded promotion projection: cold prefix once, new prefill/decode per turn."""

import math

import pytest

from keeprollming.routing.balancing import (
    BalanceConfigurationError,
    BalancePromotionPolicy,
    estimate_promotion_benefit,
    parse_promotion_policy,
    parse_reservation_policy,
)


def estimate(policy=None, *, prompt=100_000, cached=90_000, output=500, current=None, candidate=None):
    return estimate_promotion_benefit(
        prompt_tokens=prompt,
        cached_prompt_tokens=cached,
        completion_tokens=output,
        current_metrics=current or {"prompt_tps": 500, "completion_tps": 20},
        candidate_metrics=candidate or {"prompt_tps": 1000, "completion_tps": 100},
        promotion=policy,
    )


def test_default_projection_can_repay_migration_on_later_turns():
    current_only = estimate(BalancePromotionPolicy(lookahead_turns=0))
    future = estimate()
    assert current_only["decision"] == "stay_pinned"
    assert future["decision"] == "promote"
    assert future["assumptions_source"] == "configured"
    assert future["lookahead_turns"] == 5
    assert future["expected_new_prompt_tokens"] == 8192
    assert future["expected_completion_tokens"] == 1024
    assert future["estimated_current_turn_seconds"] == 45
    assert future["estimated_candidate_turn_seconds"] == 105
    assert future["estimated_prefill_cost_seconds"] == 80
    assert future["estimated_future_prefill_savings_seconds"] == pytest.approx(40.960)
    assert future["estimated_future_decode_savings_seconds"] == pytest.approx(204.800)
    assert future["estimated_current_seconds"] == pytest.approx(382.920)
    assert future["estimated_candidate_seconds"] == pytest.approx(197.160)
    assert future["estimated_net_savings_seconds"] == pytest.approx(185.760)


def test_large_api_cap_does_not_become_current_output_prediction():
    small = estimate(output=1024)
    large = estimate(output=65536)
    assert small["estimated_current_seconds"] == large["estimated_current_seconds"]
    assert small["estimated_candidate_seconds"] == large["estimated_candidate_seconds"]
    assert large["completion_tokens_budget"] == 65536
    assert large["completion_tokens_forecast"] == 1024


@pytest.mark.parametrize("cap,expected", [(128, 128), (65536, 2048)])
def test_current_forecast_is_configurable_and_bounded_by_api_cap(cap, expected):
    policy = parse_promotion_policy({"promotion": {"expected_current_completion_tokens": 2048}}, route_name="r")
    assert estimate(policy, output=cap)["completion_tokens_forecast"] == expected


@pytest.mark.parametrize("invalid", [True, -1, 0.5, "auto", math.inf])
def test_current_forecast_rejects_invalid_config(invalid):
    with pytest.raises(BalanceConfigurationError):
        parse_promotion_policy({"promotion": {"expected_current_completion_tokens": invalid}}, route_name="r")


@pytest.mark.parametrize("turns", [0, 0.5, 1, 5, 1000])
def test_projection_uses_constant_arithmetic_not_per_turn_iteration(turns):
    # An analytic equality also guards against multiplying the cold prefix
    # processing cost by lookahead turns.
    result = estimate(BalancePromotionPolicy(lookahead_turns=turns))
    assert result["estimated_current_seconds"] == pytest.approx(round(45 + turns * (8192 / 500 + 1024 / 20), 3))
    assert result["estimated_candidate_seconds"] == pytest.approx(round(105 + turns * (8192 / 1000 + 1024 / 100), 3))


def test_future_prefill_alone_can_justify_promotion():
    result = estimate(
        BalancePromotionPolicy(expected_completion_tokens=0),
        current={"prompt_tps": 100, "completion_tps": 100},
        candidate={"prompt_tps": 1000, "completion_tps": 100},
    )
    assert result["estimated_current_turn_seconds"] == result["estimated_candidate_turn_seconds"]
    assert result["estimated_future_prefill_savings_seconds"] > 0
    assert result["estimated_future_decode_savings_seconds"] == 0
    assert result["decision"] == "promote"


def test_future_decode_alone_can_justify_promotion():
    result = estimate(BalancePromotionPolicy(expected_new_prompt_tokens=0))
    assert result["estimated_future_prefill_savings_seconds"] == 0
    assert result["estimated_future_decode_savings_seconds"] > 0
    assert result["decision"] == "promote"


def test_slower_future_prefill_must_offset_faster_decode():
    result = estimate(
        BalancePromotionPolicy(expected_new_prompt_tokens=100_000, expected_completion_tokens=1),
        prompt=1000,
        cached=0,
        output=1000,
        current={"prompt_tps": 1000, "completion_tps": 20},
        candidate={"prompt_tps": 100, "completion_tps": 100},
    )
    assert result["estimated_candidate_turn_seconds"] < result["estimated_current_turn_seconds"]
    assert result["estimated_future_prefill_savings_seconds"] < 0
    assert result["decision"] == "stay_pinned"


def test_identical_rates_and_zero_cache_do_not_cause_churn():
    result = estimate(cached=0, candidate={"prompt_tps": 500, "completion_tps": 20})
    assert result["estimated_net_savings_seconds"] == 0
    assert result["decision"] == "stay_pinned"


def test_large_cached_prefix_can_still_outweigh_future_gains():
    result = estimate(prompt=1_000_000, cached=1_000_000)
    assert result["estimated_net_savings_seconds"] < 0
    assert result["decision"] == "stay_pinned"


def test_numeric_overflow_fails_closed():
    result = estimate(BalancePromotionPolicy(lookahead_turns=1e308))
    assert result["decision"] == "invalid_estimate"


def test_default_and_inherited_policy():
    assert parse_promotion_policy(None, route_name="r") == BalancePromotionPolicy()
    assert parse_reservation_policy({}, route_name="r").promotion == BalancePromotionPolicy()
    configured = {
        "promotion": {"lookahead_turns": 0.5, "expected_new_prompt_tokens": 100, "expected_completion_tokens": 50}
    }
    policy = parse_reservation_policy(configured, route_name="r").promotion
    assert policy == BalancePromotionPolicy(0.5, 100, 50)


@pytest.mark.parametrize("value", [None, False, [], "auto"])
def test_invalid_policy_mapping(value):
    with pytest.raises(BalanceConfigurationError, match="promotion"):
        parse_reservation_policy({"promotion": value}, route_name="r")


@pytest.mark.parametrize(
    "key,value",
    [
        ("lookahead_turns", -1),
        ("lookahead_turns", True),
        ("lookahead_turns", "5"),
        ("lookahead_turns", math.inf),
        ("lookahead_turns", math.nan),
        ("expected_new_prompt_tokens", -1),
        ("expected_new_prompt_tokens", True),
        ("expected_new_prompt_tokens", 1.5),
        ("expected_completion_tokens", "1024"),
        ("expected_completion_tokens", -1),
        ("unexpected", 1),
    ],
)
def test_invalid_policy_fields(key, value):
    with pytest.raises(BalanceConfigurationError, match="promotion"):
        parse_reservation_policy({"promotion": {key: value}}, route_name="r")


@pytest.mark.parametrize("invalid", [math.inf, math.nan, 0, -1])
def test_invalid_measured_rates_do_not_promote(invalid):
    result = estimate(current={"prompt_tps": invalid, "completion_tps": 20})
    assert result["decision"] == "insufficient_metrics"


def test_startup_validation_rejects_bad_promotion():
    from keeprollming.config import load_user_routes, validate_resolved_route_balance

    routes = load_user_routes(
        {
            "routes": {
                "pool": {"model": "m", "upstream_url": "http://unused"},
                "selector": {"balance": {"targets": ["pool"], "promotion": {"lookahead_turns": -1}}},
            }
        }
    )
    with pytest.raises(ValueError, match="lookahead_turns"):
        validate_resolved_route_balance(routes)


def test_estimator_only_reads_fixed_rate_and_margin_values_and_performs_no_io(monkeypatch):
    import builtins

    class Rates(dict):
        def __init__(self, **values):
            super().__init__(values)
            self.reads = []

        def get(self, key, default=None):
            self.reads.append(key)
            return super().get(key, default)

        def __iter__(self):
            raise AssertionError("No history/metric scans in projection")

    def no_io(*_args, **_kwargs):
        raise AssertionError("Projection must not read files or logs")

    current = Rates(prompt_tps=500, completion_tps=20)
    candidate = Rates(prompt_tps=1000, completion_tps=100)
    monkeypatch.setattr(builtins, "open", no_io)
    result = estimate(BalancePromotionPolicy(lookahead_turns=1_000_000), current=current, candidate=candidate)
    assert result["decision"] == "promote"
    assert current.reads == candidate.reads == ["prompt_tps", "completion_tps", "margin_ratio"]
