"""Canonical runtime event envelope for KRM observability.

Defines ``EventSource`` and ``RuntimeEvent`` as specified in the
Event Envelope Contract (TASK-20260710-003).

These dataclasses are the immutable envelope through which ALL
observability events flow before dispatch to consumers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

# ── Level hierarchy (D-072 §4) ────────────────────────────────────

#: Canonical level ordering for RuntimeEvent levels.
#: TRACE < DEBUG < INFO < BASIC < WARN < ERROR
#: Used by projectors for level filtering and by consumers for routing.
LEVEL_ORDER = ("TRACE", "DEBUG", "INFO", "BASIC", "WARN", "ERROR")


def level_at_or_above(level: str, minimum: str) -> bool:
    """Check if ``level`` is at or above ``minimum`` in the hierarchy.

    Parameters
    ----------
    level:
        The event's level.
    minimum:
        The minimum required level (e.g., projector's configured level).

    Returns
    -------
    bool
        True if ``level`` meets or exceeds ``minimum``; False otherwise.

    Raises
    ------
    ValueError
        If either argument is not a valid level.
    """
    try:
        idx_level = LEVEL_ORDER.index(level)
        idx_min = LEVEL_ORDER.index(minimum)
    except ValueError as exc:
        raise ValueError(
            f"Invalid level in comparison: {level!r} vs {minimum!r}; "
            f"valid levels are {LEVEL_ORDER}"
        ) from exc
    return idx_level >= idx_min


@dataclass(frozen=True, slots=True)
class EventSource:
    """Identifies the origin component of a runtime event.

    The ``namespace`` property derives the permanent hierarchical
    namespace from domain + component (+ optional instance).
    """
    domain: str
    component: str
    instance: Optional[str] = None

    @property
    def namespace(self) -> str:
        """Derive hierarchical namespace from domain + component + instance."""
        ns = f"{self.domain}.{self.component}"
        if self.instance:
            ns = f"{ns}.{self.instance}"
        return ns


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """Minimum runtime event envelope for KRM observability.

    All events flow through this envelope before dispatch to consumers.
    The envelope is immutable (frozen) to prevent mutation after creation.

    Invariants:
    - E-01: type is a dot-separated namespace
    - E-02: timestamp_ns is wall-clock occurrence time
    - E-04: data preserves runtime-fact fidelity
    - E-07: level is one of TRACE/DEBUG/INFO/BASIC/WARN/ERROR
    - E-09: shallow-immutable (frozen dataclass)
    - INV-06: req_id is exclusively envelope correlation metadata,
      not duplicated in data

    Usage::

        event = RuntimeEvent(
            type="streaming.parser.event",
            timestamp_ns=time.time_ns(),
            source=EventSource(domain="streaming", component="parser"),
            data={"event_type": "AssistantTextDelta", "delta_len": 42},
            req_id="abc123",
            level="DEBUG",
        )
    """
    # ── Required fields (no defaults) ────────────────────────────

    #: Permanent hierarchical event type (e.g., "streaming.parser.event").
    #: Must match one of the namespaces defined in OBSERVABILITY_EVENT_CATALOG_V1.md §8.
    #: Never use transitional names (v2_*, temp_*, etc.).
    type: str

    #: Origin component metadata (domain, component, instance).
    source: EventSource

    #: Event-specific payload. Preserves runtime-fact fidelity.
    #: JSON safety, redaction, snipping and formatting belong to
    #: consumer-specific projections.
    data: dict[str, Any] = field(default_factory=dict)

    #: Nanosecond-precision UTC timestamp at event creation.
    #: Use time.time_ns() for creation.
    timestamp_ns: int = field(default_factory=time.time_ns)

    # ── Optional/conditional fields (all have defaults) ──────────

    #: Request correlator. Required for all request-scoped events.
    #: Optional for system-level events (system.starting, system.stopping).
    #: INV-06: req_id is exclusively envelope correlation metadata.
    #: It must NOT appear in data. This prevents duplication.
    req_id: Optional[str] = None

    #: Log level. Maps to consumer routing and projector filtering.
    #: Level hierarchy (ascending): TRACE < DEBUG < INFO < BASIC < WARN < ERROR
    #: TRACE → all events including diagnostic/internal/transient
    #: DEBUG → operational detail including filter state, parsing detail
    #: INFO → principal request/response lifecycle events
    #: BASIC → essential operational record (D-072 §4)
    #: WARN → warnings
    #: ERROR → errors
    level: str = "INFO"

    #: Distributed tracing correlator. Not required for initial implementation.
    trace_id: Optional[str] = None

    #: Span within a trace. Not required for initial implementation.
    span_id: Optional[str] = None

    # ── Validation ──────────────────────────────────────────────

    def __post_init__(self) -> None:
        """Validate envelope invariants."""
        # E-01: type must be a valid namespace (dot-separated, no empty segments)
        if not self.type or "." not in self.type:
            raise ValueError(
                f"RuntimeEvent.type must be a dot-separated namespace, got: {self.type!r}"
            )

        # E-07: level must be a valid log level
        if self.level not in ("TRACE", "DEBUG", "INFO", "BASIC", "WARN", "ERROR"):
            raise ValueError(
                f"RuntimeEvent.level must be TRACE/DEBUG/INFO/BASIC/WARN/ERROR, got: {self.level!r}"
            )

        # data must be a dict
        if not isinstance(self.data, dict):
            raise TypeError(
                f"RuntimeEvent.data must be a dict, got: {type(self.data).__name__}"
            )
