"""Contract tests for the built-in filter-module registry."""

import pytest

from keeprollming.filters import (
    built_in_filter_modules,
    request_priorities,
    stream_priorities,
    validate_filter_module_settings,
)


def test_registry_is_the_canonical_catalogue_for_builtin_filters():
    modules = built_in_filter_modules()

    assert set(modules) == {
        "system_prompt",
        "summarization",
        "tool_rewrite",
        "reasoning_loop_stopper",
        "model_tool_loop_stopper",
        "multimodal_validator",
        "model_nudge",
        "timestamp",
    }
    assert request_priorities() == {
        name: module.request_priority for name, module in modules.items()
    }


def test_registry_factory_builds_the_declared_filter_module():
    module = built_in_filter_modules()["system_prompt"]
    instance = module.request_factory(config={"enabled": True, "prompt": "Hello"})

    assert instance.name == "system_prompt"
    assert instance.priority == module.request_priority


def test_registry_declares_stream_priorities_only_for_stream_modules():
    assert stream_priorities() == {
        "tool_rewrite": 15,
        "reasoning_loop_stopper": 60,
        "model_tool_loop_stopper": 55,
        "model_nudge": 50,
        "timestamp": 20,
    }


def test_registry_rejects_unknown_or_wrongly_typed_module_settings():
    with pytest.raises(ValueError, match="does not support setting"):
        validate_filter_module_settings({"timestamp": {"enabled": True, "bogus": 1}})
    with pytest.raises(ValueError, match="model_nudge.max_attempts"):
        validate_filter_module_settings({"model_nudge": {"max_attempts": "three"}})
