"""Observability module — re-exports from canonical performance and metrics modules.

This package provides a unified import path for observability concerns.
The canonical implementations live in keeprollming.performance and keeprollming.metrics.
"""

from ..performance import (  # noqa: F401
    set_performance_logs_dir,
    set_summary_interval,
    record_request_performance,
    _ensure_dir,
)

from ..metrics import (  # noqa: F401
    METRICS_COLLECTOR,
    ConversationMetrics,
    record_conversation_metrics,
    record_summary_cache_hit,
    record_summary_cache_miss,
    record_summary_reuse,
)

__all__ = [
    "set_performance_logs_dir",
    "set_summary_interval",
    "record_request_performance",
    "_ensure_dir",
    "METRICS_COLLECTOR",
    "ConversationMetrics",
    "record_conversation_metrics",
    "record_summary_cache_hit",
    "record_summary_cache_miss",
    "record_summary_reuse",
]
