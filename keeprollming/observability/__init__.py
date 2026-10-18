"""Observability module — EventDispatcher, consumers, and event envelope.

This package provides the publication substrate for KRM observability.

Exports:
- EventDispatcher (dispatcher.py)
- RuntimeEvent, EventSource (events.py)
- LoggerConsumer, MetricsConsumer, PerformanceConsumer (consumers.py)
- Formatter, JsonFormatter, PlainTextFormatter, CompactFormatter (formatters.py)
- Route, RoutingEngine (routing.py)
"""

from .consumers import LoggerConsumer, MetricsConsumer, PerformanceConsumer
from .dispatcher import EventDispatcher
from .events import EventSource, RuntimeEvent
from .events_downstream import (  # noqa: F401
    emit_chunk_sent,
    emit_delivery_closed,
    emit_delivery_completed,
    emit_delivery_failed,
    emit_downstream_event,
)
from .events_execution_accounting import (  # noqa: F401
    emit_attempt_recorded,
    emit_execution_accounting_event,
    emit_usage_captured,
    emit_usage_finalized,
)

# O8 event modules (new permanent namespaces)
from .events_request import (  # noqa: F401
    emit_cancelled,
    emit_completed,
    emit_preprocessing_completed,
    emit_preprocessing_started,
    emit_received,
    emit_request_event,
)
from .events_request import (
    emit_failed as emit_request_failed,
)
from .events_routing import (
    emit_failed as emit_routing_failed,
)
from .events_routing import (
    emit_resolved as emit_routing_resolved,
)
from .events_routing import (  # noqa: F401
    emit_routing_event,
)
from .events_routing import (
    emit_started as emit_routing_started,
)
from .events_streaming_parser import (  # noqa: F401
    emit_events_generated,
    emit_flushed,
    emit_frame_received,
    emit_invalid_frame,
    emit_streaming_parser_event,
    emit_usage_buffered,
)
from .events_tool_rewrite import (  # noqa: F401
    emit_body_error,
    emit_parse_error,
    emit_streaming_error,
    emit_tool_rewrite_event,
)
from .formatters import CompactFormatter, Formatter, JsonFormatter, PlainTextFormatter
from .raw_trace_consumer import RawTraceConsumer
from .routing import Route, RoutingEngine

__all__ = [
    "EventDispatcher",
    "EventSource",
    "RuntimeEvent",
    "LoggerConsumer",
    "MetricsConsumer",
    "PerformanceConsumer",
    # Phase O5 — formatter and routing
    "Formatter",
    "JsonFormatter",
    "PlainTextFormatter",
    "CompactFormatter",
    "Route",
    "RoutingEngine",
    "RawTraceConsumer",
    "emit_request_failed",
    "emit_routing_failed",
    "emit_routing_resolved",
    "emit_routing_started",
]
