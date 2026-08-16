"""File-backed system-prompt configuration contracts."""

from __future__ import annotations

import pytest

from keeprollming.filters import validate_filter_module_settings
from keeprollming.filters.system_prompt import materialize_prompt_file


def test_materialize_prompt_file_resolves_relative_to_configuration(tmp_path):
    prompt = tmp_path / "prompts" / "architect.md"
    prompt.parent.mkdir()
    prompt.write_text("You are the architect.\n", encoding="utf-8")

    result = materialize_prompt_file(
        {"enabled": True, "prompt_file": "prompts/architect.md", "override": True},
        config_directory=tmp_path,
        route_name="code/architect",
    )

    assert result == {
        "enabled": True,
        "prompt": "You are the architect.\n",
        "override": True,
    }


def test_materialize_prompt_file_fails_early_for_missing_file(tmp_path):
    with pytest.raises(ValueError, match="code/executor.*missing.md"):
        materialize_prompt_file(
            {"prompt_file": "prompts/missing.md"},
            config_directory=tmp_path,
            route_name="code/executor",
        )


def test_system_prompt_rejects_ambiguous_inline_and_file_sources():
    with pytest.raises(ValueError, match="either 'prompt' or 'prompt_file'"):
        validate_filter_module_settings(
            {"system_prompt": {"prompt": "inline", "prompt_file": "prompt.md"}}
        )
