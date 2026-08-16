"""Typed settings for the model-nudge filter module.

This module intentionally receives a mapping from the root route configuration;
it never reads YAML or environment variables itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from keeprollming.orchestrator.filter import FilterConfig
from keeprollming.filters.contracts import FilterSettingsSchema


SCHEMA = FilterSettingsSchema({
    "trigger_patterns": list, "action": str, "nudge_message": str,
    "max_attempts": int, "nudge_on_empty": bool,
    "nudge_fallback_message": str, "max_retries": int,
    "tail_buffer_size": int, "upstream_url": str, "api_key": str,
})


@dataclass
class ModelNudgeConfig(FilterConfig):
    """Configuration shared by non-streaming and nudge hooks."""

    max_retries: int = 2
    retry_timeout: int = 120
    trigger_patterns: List[str] = field(default_factory=lambda: [":$"])
    action: str = "nudge"
    nudge_message: str = "Continue."
    max_attempts: int = 3
    nudge_on_empty: bool = False
    nudge_fallback_message: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.name = "model_nudge"
