"""Tests for the canonical route ``filters`` configuration contract."""

from __future__ import annotations

import pytest

from keeprollming.filters import FilterConfigurationError, normalize_filters


_PRIORITIES = {"system_prompt": 10, "model_nudge": 50, "timestamp": 100}


def test_normalize_filters_assigns_module_default_priority_without_yaml_order():
    result = normalize_filters(
        {
            "timestamp": {"enabled": True},
            "model_nudge": {"enabled": True, "max_attempts": 3},
        },
        default_priorities=_PRIORITIES,
    )

    assert result["timestamp"]["priority"] == 100
    assert result["model_nudge"]["priority"] == 50


def test_normalize_filters_accepts_explicit_route_priority_override():
    result = normalize_filters(
        {"timestamp": {"enabled": True, "priority": 110}},
        default_priorities=_PRIORITIES,
    )

    assert result["timestamp"]["priority"] == 110


@pytest.mark.parametrize(
    "filters, error",
    [
        ({"unknown": {"enabled": True}}, "unknown filter"),
        ({"timestamp": True}, "must be a mapping"),
        ({"timestamp": {"enabled": "yes"}}, "must be a boolean"),
        ({"timestamp": {"priority": "last"}}, "must be an integer"),
        (
            {
                "system_prompt": {"enabled": True, "priority": 10},
                "model_nudge": {"enabled": True, "priority": 10},
            },
            "same effective priority",
        ),
    ],
)
def test_normalize_filters_rejects_ambiguous_or_invalid_configuration(filters, error):
    with pytest.raises(FilterConfigurationError, match=error):
        normalize_filters(filters, default_priorities=_PRIORITIES)
