"""Typed settings and source loading for system-prompt request processing."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from keeprollming.filters.contracts import FilterSettingsSchema

SCHEMA = FilterSettingsSchema({"prompt": str, "prompt_file": str, "override": bool})


def validate_settings(settings: Mapping[str, Any]) -> None:
    """Validate the mutually exclusive prompt-source settings."""
    if "prompt" in settings and "prompt_file" in settings:
        raise ValueError(
            "filter 'system_prompt' accepts either 'prompt' or 'prompt_file', not both"
        )
    if "prompt_file" in settings:
        prompt_file = settings["prompt_file"]
        if not isinstance(prompt_file, str):
            raise ValueError("filter 'system_prompt.prompt_file' must be str")
        if not prompt_file.strip():
            raise ValueError("filter 'system_prompt.prompt_file' must not be empty")


def materialize_prompt_file(
    settings: Mapping[str, Any],
    *,
    config_directory: Path,
    route_name: str,
) -> dict[str, Any]:
    """Replace a configured relative prompt file with its UTF-8 content.

    This happens when configuration is loaded, not while a request is being
    processed.  Therefore a missing or invalid file prevents a server with an
    invalid route from starting (or being hot-reloaded).
    """
    result = deepcopy(dict(settings))
    validate_settings(result)
    prompt_file = result.get("prompt_file")
    if prompt_file is None:
        return result

    path = Path(prompt_file).expanduser()
    if not path.is_absolute():
        path = config_directory / path
    path = path.resolve()
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(
            "invalid system prompt file for route "
            f"'{route_name}': {prompt_file!r} ({exc})"
        ) from exc

    result["prompt"] = content
    del result["prompt_file"]
    return result
