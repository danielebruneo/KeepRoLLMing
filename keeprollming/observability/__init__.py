"""Observability module — EventDispatcher, consumers, and event envelope.

This package provides the publication substrate for KRM observability.

Exports:
- EventDispatcher (dispatcher.py)
- RuntimeEvent, EventSource (events.py)
- LoggerConsumer, MetricsConsumer, PerformanceConsumer (consumers.py)
- Formatter, JsonFormatter, PlainTextFormatter, CompactFormatter (formatters.py)
- Route, RoutingEngine (routing.py)
"""

from .dispatcher import EventDispatcher
from .events import EventSource, RuntimeEvent
from .consumers import LoggerConsumer, MetricsConsumer, PerformanceConsumer
from .formatters import Formatter, JsonFormatter, PlainTextFormatter, CompactFormatter
from .routing import Route, RoutingEngine
from .raw_trace_consumer import RawTraceConsumer

# O8 event modules (new permanent namespaces)
from .events_request import (  # noqa: F401
    emit_request_event,
    emit_received,
    emit_preprocessing_started,
    emit_preprocessing_completed,
    emit_completed,
    emit_failed as emit_request_failed,
    emit_cancelled,
)
from .events_routing import (  # noqa: F401
    emit_routing_event,
    emit_started as emit_routing_started,
    emit_resolved as emit_routing_resolved,
    emit_failed as emit_routing_failed,
)
from .events_downstream import (  # noqa: F401
    emit_downstream_event,
    emit_chunk_sent,
    emit_delivery_completed,
    emit_delivery_closed,
    emit_delivery_failed,
)
from .events_streaming_parser import (  # noqa: F401
    emit_streaming_parser_event,
    emit_frame_received,
    emit_events_generated,
    emit_usage_buffered,
    emit_flushed,
    emit_invalid_frame,
)
from .events_execution_accounting import (  # noqa: F401
    emit_execution_accounting_event,
    emit_usage_captured,
    emit_attempt_recorded,
    emit_usage_finalized,
)
from .events_tool_rewrite import (  # noqa: F401
    emit_tool_rewrite_event,
    emit_parse_error,
    emit_streaming_error,
    emit_body_error,
)

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
]
