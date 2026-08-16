"""Pipeline integration for canonical route ``filters`` configuration."""

from __future__ import annotations

import pytest

from keeprollming.filters import FilterConfigurationError
from keeprollming.orchestrator.pipeline import Pipeline


def test_pipeline_uses_module_priorities_not_yaml_key_order():
    pipeline = Pipeline.from_route_config(
        {
            "timestamp": {"enabled": True},
            "system_prompt": {"enabled": True, "prompt": "Be concise."},
        }
    )

    assert pipeline is not None
    assert [filter_.name for filter_ in pipeline.filters] == [
        "system_prompt",
        "timestamp",
    ]


def test_pipeline_honours_explicit_route_priority_override():
    pipeline = Pipeline.from_route_config(
        {
            "timestamp": {"enabled": True, "priority": 5},
            "system_prompt": {"enabled": True, "prompt": "Be concise."},
        }
    )

    assert pipeline is not None
    assert [filter_.name for filter_ in pipeline.filters] == [
        "timestamp",
        "system_prompt",
    ]


def test_legacy_nested_filter_shape_is_rejected():
    """The removed ``filter_chain`` shape cannot be interpreted as filters."""
    with pytest.raises(FilterConfigurationError, match="unknown filter 'order'"):
        Pipeline.from_route_config(
            {
                "order": ["system_prompt"],
                "filters": {"system_prompt": {"enabled": True}},
            }
        )
