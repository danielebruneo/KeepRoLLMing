"""
Test: Pipeline.from_route_config with simplified format (no 'order' key).

Verifies that the method correctly:
1. Returns a non-None Pipeline when valid enabled filters are present
2. Skips disabled filters (multimodal_validator)
3. Skips unnamed filters (only configured names are included)
4. Orders filters by their priority attribute
"""

import sys
import os

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keeprollming.orchestrator.pipeline import Pipeline


def test_from_route_config_simplified():
    """Test the simplified config format: flat names under 'filters:', no 'order'."""

    route_config = {
        "filters": {
            "multimodal_validator": {"enabled": False},
            "timestamp": {"enabled": True},
            "model_tool_loop_stopper": {"enabled": True},
            "system_prompt": {"enabled": True, "override": False, "prompt": "You are a helpful assistant."},
        }
    }

    pipeline = Pipeline.from_route_config(route_config)

    # 1. Pipeline must not be None (we have enabled filters)
    assert pipeline is not None, "Pipeline should not be None when valid enabled filters exist"

    # 2. Check which filters made it into the pipeline
    filter_names = [f.name for f in pipeline.filters]
    print(f"Filters in pipeline (in order): {filter_names}")

    # 3. Disabled filter must NOT be present
    assert "multimodal_validator" not in filter_names, (
        f"multimodal_validator should be excluded (disabled), but got: {filter_names}"
    )

    # 4. Enabled filters MUST be present
    assert "timestamp" in filter_names, "timestamp should be present (enabled)"
    assert "model_tool_loop_stopper" in filter_names, "model_tool_loop_stopper should be present (enabled)"
    assert "system_prompt" in filter_names, "system_prompt should be present (enabled)"

    # 5. Unmentioned filters must NOT be present
    unmentioned = {"summarization", "tool_rewrite", "reasoning_loop_stopper", "model_nudge"}
    for name in unmentioned:
        assert name not in filter_names, (
            f"{name} should NOT be present (not in config), but got: {filter_names}"
        )

    # 6. Verify priority order: system_prompt (10) < model_tool_loop_stopper (25) < timestamp (100)
    sp_idx = filter_names.index("system_prompt")
    tls_idx = filter_names.index("model_tool_loop_stopper")
    ts_idx = filter_names.index("timestamp")

    assert sp_idx < tls_idx, (
        f"system_prompt (priority 10) should come before model_tool_loop_stopper (priority 25), "
        f"but got order: {filter_names}"
    )
    assert tls_idx < ts_idx, (
        f"model_tool_loop_stopper (priority 25) should come before timestamp (priority 100), "
        f"but got order: {filter_names}"
    )

    # 7. Verify the priority values directly on the filter instances
    priorities = [(f.name, f.priority) for f in pipeline.filters]
    print(f"Filter priorities: {priorities}")

    for i in range(len(priorities) - 1):
        assert priorities[i][1] <= priorities[i + 1][1], (
            f"Filters not sorted by priority: {priorities}"
        )

    print("\nAll assertions passed!")


def test_from_route_config_no_filters_key():
    """Test config without the 'filters' wrapper — flat keys directly."""

    route_config = {
        "system_prompt": {"enabled": True},
        "timestamp": {"enabled": True},
    }

    pipeline = Pipeline.from_route_config(route_config)
    assert pipeline is not None
    filter_names = [f.name for f in pipeline.filters]
    assert "system_prompt" in filter_names
    assert "timestamp" in filter_names


def test_from_route_config_none():
    """Test that None/empty returns None."""
    assert Pipeline.from_route_config(None) is None
    assert Pipeline.from_route_config({}) is None


if __name__ == "__main__":
    test_from_route_config_simplified()
    test_from_route_config_no_filters_key()
    test_from_route_config_none()
    print("\n=== All tests passed! ===")
