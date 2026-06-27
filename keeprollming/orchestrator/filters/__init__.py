"""
Filter implementations for the LLM Orchestrator.

Importing this package auto-registers all filters via @register_filter.
New filters are automatically discovered when their module is imported.

Usage:
    from keeprollming.orchestrator.filters import (
        SystemPromptFilter, ToolLoopStopperFilter, ModelNudgeFilter,
        SummarizationFilter, ToolRewriteFilter, TimestampFilter,
        StreamingFilterBase,
    )
    from keeprollming.orchestrator.filter import get_registered_filters
"""

# Import streaming filter base first
from .streaming_filter import StreamingFilterBase, StreamingFilterConfig  # noqa: F401

# Import all filters to trigger @register_filter auto-registration
from .system_prompt_filter import SystemPromptFilter  # noqa: F401
from .tool_loop_stopper import ToolLoopStopperFilter  # noqa: F401
from .model_nudge_filter import ModelNudgeFilter  # noqa: F401
from .summarization_filter import SummarizationFilter  # noqa: F401
from .tool_rewrite_filter import ToolRewriteFilter  # noqa: F401
from .upstream_filter import UpstreamFilter  # noqa: F401
from .timestamp_filter import TimestampFilter  # noqa: F401
from .multimodal_validator_filter import MultimodalValidatorFilter  # noqa: F401
from .reasoning_loop_stopper import ReasoningLoopStopperFilter  # noqa: F401

__all__ = [
    "StreamingFilterBase",
    "StreamingFilterConfig",
    "SystemPromptFilter",
    "ToolLoopStopperFilter",
    "ModelNudgeFilter",
    "SummarizationFilter",
    "ToolRewriteFilter",
    "UpstreamFilter",
    "TimestampFilter",
    "MultimodalValidatorFilter",
    "ReasoningLoopStopperFilter",
]
