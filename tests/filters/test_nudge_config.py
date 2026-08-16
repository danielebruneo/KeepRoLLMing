"""Typed model-nudge module configuration."""

from keeprollming.filters.nudge import ModelNudgeConfig


def test_nudge_module_config_declares_canonical_defaults():
    config = ModelNudgeConfig()

    assert config.name == "model_nudge"
    assert config.max_attempts == 3
    assert config.trigger_patterns == [":$"]
