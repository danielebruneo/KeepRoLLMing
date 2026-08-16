"""Built-in filter module metadata.

The registry is the single catalogue used by configuration validation and the
request pipeline.  Imports are deliberately lazy so importing configuration
does not import the HTTP/filter runtime during application bootstrap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from keeprollming.filters.multimodal_validator import SCHEMA as MULTIMODAL_SCHEMA
from keeprollming.filters.nudge.config import SCHEMA as NUDGE_SCHEMA
from keeprollming.filters.reasoning_loop_stopper import SCHEMA as RLS_SCHEMA
from keeprollming.filters.summarization import SCHEMA as SUMMARIZATION_SCHEMA
from keeprollming.filters.system_prompt import (
    SCHEMA as SYSTEM_PROMPT_SCHEMA,
)
from keeprollming.filters.system_prompt import (
    validate_settings as validate_system_prompt_settings,
)
from keeprollming.filters.timestamp import SCHEMA as TIMESTAMP_SCHEMA
from keeprollming.filters.tool_loop_stopper import SCHEMA as TLS_SCHEMA
from keeprollming.filters.tool_rewrite import SCHEMA as TOOL_REWRITE_SCHEMA
from keeprollming.orchestrator.filter import Filter


@dataclass(frozen=True)
class FilterModule:
    """Runtime metadata declared by one built-in filter module."""

    name: str
    request_factory: Callable[..., Filter]
    request_priority: int
    stream_priority: int | None = None
    config_types: Mapping[str, type | tuple[type, ...]] = ()
    settings_validator: Callable[[Mapping[str, Any]], None] | None = None

    def validate_config(self, config: Mapping[str, Any]) -> None:
        """Validate operator-supplied settings owned by this module."""
        unknown = set(config) - {"enabled", "priority"} - set(self.config_types)
        if unknown:
            raise ValueError(
                f"filter '{self.name}' does not support setting(s): "
                + ", ".join(sorted(unknown))
            )
        for key, expected_type in self.config_types.items():
            value = config.get(key)
            if value is not None and not isinstance(value, expected_type):
                raise ValueError(
                    f"filter '{self.name}.{key}' must be "
                    f"{_type_name(expected_type)}"
                )
        if self.settings_validator is not None:
            self.settings_validator(config)


def _type_name(expected: type | tuple[type, ...]) -> str:
    types = expected if isinstance(expected, tuple) else (expected,)
    return " or ".join(type_.__name__ for type_ in types)


def built_in_filter_modules() -> dict[str, FilterModule]:
    """Return the canonical built-in registry, keyed by operator-facing name."""
    from keeprollming.filters.multimodal_validator import MultimodalValidatorFilter
    from keeprollming.filters.nudge import ModelNudgeFilter
    from keeprollming.filters.reasoning_loop_stopper import ReasoningLoopStopperFilter
    from keeprollming.filters.summarization import SummarizationFilter
    from keeprollming.filters.system_prompt import SystemPromptFilter
    from keeprollming.filters.timestamp import TimestampFilter
    from keeprollming.filters.tool_loop_stopper import ToolLoopStopperFilter
    from keeprollming.filters.tool_rewrite import ToolRewriteFilter

    classes = {
        "system_prompt": (
            SystemPromptFilter,
            None,
            SYSTEM_PROMPT_SCHEMA.fields,
            validate_system_prompt_settings,
        ),
        "summarization": (SummarizationFilter, None, SUMMARIZATION_SCHEMA.fields),
        "tool_rewrite": (ToolRewriteFilter, 15, TOOL_REWRITE_SCHEMA.fields),
        "reasoning_loop_stopper": (ReasoningLoopStopperFilter, 60, RLS_SCHEMA.fields),
        "model_tool_loop_stopper": (ToolLoopStopperFilter, 55, TLS_SCHEMA.fields),
        "multimodal_validator": (MultimodalValidatorFilter, None, MULTIMODAL_SCHEMA.fields),
        "model_nudge": (ModelNudgeFilter, 50, NUDGE_SCHEMA.fields),
        "timestamp": (TimestampFilter, 20, TIMESTAMP_SCHEMA.fields),
    }
    return {
        name: FilterModule(
            name=name,
            request_factory=values[0],
            request_priority=int(values[0].priority),
            stream_priority=values[1],
            config_types=values[2],
            settings_validator=values[3] if len(values) > 3 else None,
        )
        for name, values in classes.items()
    }


def request_priorities() -> dict[str, int]:
    """Effective built-in request phase defaults, keyed by module name."""
    return {
        name: module.request_priority
        for name, module in built_in_filter_modules().items()
    }


def stream_priorities() -> dict[str, int]:
    """Built-in streaming defaults, excluding request-only modules."""
    return {
        name: module.stream_priority
        for name, module in built_in_filter_modules().items()
        if module.stream_priority is not None
    }


def validate_filter_module_settings(
    filters: Mapping[str, Mapping[str, Any]],
) -> None:
    """Validate settings against the canonical built-in module schemas."""
    modules = built_in_filter_modules()
    for name, config in filters.items():
        modules[name].validate_config(config)
