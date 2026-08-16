"""Canonical route ``filters`` normalization and validation.

Configuration is supplied by the root YAML as ``routes.<name>.filters``. This
module deliberately gives no semantic meaning to YAML mapping order: a filter
module supplies its default priority and a route may override it explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


class FilterConfigurationError(ValueError):
    """Raised when a route's canonical ``filters`` mapping is invalid."""


def normalize_filters(
    filters: Mapping[str, Any] | None,
    *,
    default_priorities: Mapping[str, int],
) -> dict[str, dict[str, Any]]:
    """Validate and normalize canonical route filter configuration.

    The returned mapping contains fresh mutable dictionaries. Each configured
    filter has an effective integer ``priority``; disabled filters are kept so
    a route can document an intentionally inactive module. Unknown filters,
    non-mapping settings and equal effective priorities are rejected early.
    """
    if filters is None:
        return {}
    if not isinstance(filters, Mapping):
        raise FilterConfigurationError("routes.<name>.filters must be a mapping")

    normalized: dict[str, dict[str, Any]] = {}
    enabled_priorities: dict[int, str] = {}
    for name, raw_config in filters.items():
        if name not in default_priorities:
            raise FilterConfigurationError(f"unknown filter '{name}'")
        if not isinstance(raw_config, Mapping):
            raise FilterConfigurationError(
                f"filter '{name}' configuration must be a mapping"
            )

        config = deepcopy(dict(raw_config))
        enabled = config.get("enabled", True)
        if not isinstance(enabled, bool):
            raise FilterConfigurationError(
                f"filter '{name}'.enabled must be a boolean"
            )
        config["enabled"] = enabled

        priority = config.get("priority", default_priorities[name])
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise FilterConfigurationError(
                f"filter '{name}'.priority must be an integer"
            )
        config["priority"] = priority
        if enabled:
            conflict = enabled_priorities.get(priority)
            if conflict is not None:
                raise FilterConfigurationError(
                    "filters "
                    f"'{conflict}' and '{name}' have the same effective priority "
                    f"({priority})"
                )
            enabled_priorities[priority] = name
        normalized[name] = config

    return normalized
