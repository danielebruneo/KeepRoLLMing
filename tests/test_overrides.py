"""Tests for the route-level upstream request override contract."""

from keeprollming.overrides import apply_overrides


def test_reasoning_effort_override_replaces_client_value() -> None:
    payload = {"model": "qwen3.8-27b", "reasoning_effort": "low"}

    applied = apply_overrides(payload, {"reasoning_effort": "high"})

    assert payload["reasoning_effort"] == "high"
    assert applied == [("reasoning_effort", "low", "high")]


def test_reasoning_effort_override_is_added_when_client_omits_it() -> None:
    payload = {"model": "qwen3.8-27b"}

    applied = apply_overrides(payload, {"reasoning_effort": "medium"})

    assert payload["reasoning_effort"] == "medium"
    assert applied == [("reasoning_effort", None, "medium")]


def test_qwen_top_k_override_replaces_client_value() -> None:
    payload = {"model": "qwen3.8-27b", "top_k": 20}

    applied = apply_overrides(payload, {"top_k": 45})

    assert payload["top_k"] == 45
    assert applied == [("top_k", 20, 45)]
