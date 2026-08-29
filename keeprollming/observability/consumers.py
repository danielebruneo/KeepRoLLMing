"""Minimal consumer stubs for KRM observability (Phase O2).

Each stub captures events and routes them based on level and namespace.
Actual implementation (AsyncLogWriter, RotatingFileHandler, etc.)
is deferred to Phase O3.

Invariants:
- INV-03: Logger classification, threshold filtering and LogRecord
  projection belong to the Logger Consumer responsibility boundary,
  not to RuntimeEvent producers.
- INV-04: RuntimeEvent payloads preserve runtime-fact fidelity.
  JSON safety, redaction, snipping and formatting are consumer
  responsibilities.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Any, Dict, List, Optional

from .events import RuntimeEvent
from .formatters import JsonFormatter
from .routing import RoutingEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PerformanceConsumer (O10)
# ---------------------------------------------------------------------------


class PerformanceConsumer:
    """Performance Consumer — event-driven performance logging (Phase O10).

    Subscribes to ``execution.performance.*`` events and replicates the
    exact behavior of ``record_request_performance()`` from performance.py,
    producing identical outputs:

    - Per-request JSONL records in per-route ``.requests.jsonl`` files
    - Incremental RouteStats aggregates (O(1) per request)
    - Periodic summary.yaml flush (every N requests, default 20)
    - Archive on 1000-request boundary with epoch reset

    **Parity contract:** Output is bit-identical to what the legacy
    synchronous ``record_request_performance()`` would produce for the
    same inputs. Dashboard compatibility guaranteed.

    Parameters
    ----------
    perf_logs_dir:
        Directory for performance logs. If None, uses default from
        performance module configuration.
    summary_interval:
        Number of requests between summary.yaml flushes. Default 20.
    capture:
        When True, store captured events in an in-memory list for
        testing/inspection.
    """

    def __init__(
        self,
        perf_logs_dir: Optional[str] = None,
        summary_interval: int = 20,
        capture: bool = False,
    ) -> None:
        """Initialize PerformanceConsumer.

        Parameters
        ----------
        perf_logs_dir:
            Directory for performance logs. If None, uses default from
            performance module configuration.
        summary_interval:
            Number of requests between summary.yaml flushes. Default 20.
        capture:
            When True, store captured events in an in-memory list for
            testing/inspection.
        """
        self._perf_logs_dir = perf_logs_dir
        self._summary_interval = max(1, summary_interval)
        self._capture = capture
        self._captured: List[RuntimeEvent] = []

        # Request counter for periodic summary flush and archive
        self._request_counter: int = 0
        # Production schedules this consumer asynchronously. The lock retains
        # event order while the filesystem/stat transaction runs off the loop.
        self._async_lock = asyncio.Lock()

        # Import performance module helpers for parity
        # These are the same functions used by record_request_performance()
        from ..performance import (
            RouteStats,
            _append_entry,
            _ensure_dir,
            _safe_slug,
            _seed_route_stats,
            _update_summary,
            compute_request_performance,
            reset_route_stats,
            set_performance_logs_dir,
        )

        self._compute_request_performance = compute_request_performance
        self._RouteStats = RouteStats
        self._append_entry = _append_entry
        self._seed_route_stats = _seed_route_stats
        self._update_summary = _update_summary
        self._safe_slug = _safe_slug
        self._ensure_dir = _ensure_dir
        self._reset_route_stats = reset_route_stats

        # Set perf_logs_dir in performance module if configured
        if perf_logs_dir is not None:
            set_performance_logs_dir(perf_logs_dir)

    def __call__(self, event: RuntimeEvent) -> None:
        """Process a performance event.

        Handles:
        - ``execution.performance.request_complete`` — per-request metrics
        - ``execution.app.perf_logs_dir`` — directory configuration update

        Parameters
        ----------
        event:
            The RuntimeEvent to process.
        """
        # Capture for testing
        if self._capture:
            self._captured.append(event)

        # Route by event type
        if event.type == "execution.performance.request_complete":
            self._handle_request_complete(event)
        elif event.type == "execution.app.perf_logs_dir":
            self._handle_perf_logs_dir(event)

    async def consume_async(self, event: RuntimeEvent) -> None:
        """Persist one event off the request event loop, in event order.

        The synchronous callable remains available for deterministic tests and
        explicit synchronous callers. The app subscribes this method through
        the dispatcher async path.
        """
        async with self._async_lock:
            await asyncio.to_thread(self, event)

    def _handle_request_complete(self, event: RuntimeEvent) -> None:
        """Handle execution.performance.request_complete event.

        Replicates record_request_performance() logic with full parity:
        1. Compute derived metrics (TPS, etc.)
        2. Build record dict with all fields
        3. Append JSON line to per-route .requests.jsonl file
        4. Update in-memory RouteStats aggregate
        5. Periodic summary flush every N requests
        6. Archive on 1000-request boundary
        """
        data = event.data

        # Extract fields from event
        model = data.get("model", "unknown")
        route_name = data.get("route_name", "unknown")
        route_hierarchy = data.get("route_hierarchy")
        req_id = data.get("req_id", "")
        stream = data.get("stream", False)
        elapsed_ms = data.get("elapsed_ms")
        ttft_ms = data.get("ttft_ms")
        completion_tokens = data.get("completion_tokens")
        prompt_tokens = data.get("prompt_tokens")
        total_tokens = data.get("total_tokens")
        finish_reason = data.get("finish_reason")
        did_summarize = data.get("did_summarize", False)
        passthrough = data.get("passthrough", False)
        completion_tokens_source = data.get("completion_tokens_source", "missing")

        # ExecutionUsage fields (Phase 12)
        upstream_attempts = data.get("upstream_attempts", 0)
        usage_reported_attempts = data.get("usage_reported_attempts", 0)
        recovery_count = data.get("recovery_count", 0)
        retry_amplification_ratio = data.get("retry_amplification_ratio")
        try:
            if retry_amplification_ratio is not None and not math.isfinite(
                float(retry_amplification_ratio)
            ):
                retry_amplification_ratio = None
        except (TypeError, ValueError):
            retry_amplification_ratio = None
        usage_complete = data.get("usage_complete", False)
        upstream_prompt_tokens = data.get("upstream_prompt_tokens")
        upstream_completion_tokens = data.get("upstream_completion_tokens")
        upstream_total_tokens = data.get("upstream_total_tokens")
        cached_prompt_tokens = data.get("cached_prompt_tokens")

        # Determine base directory
        if self._perf_logs_dir is not None:
            from pathlib import Path

            base_dir = Path(self._perf_logs_dir)
            base_dir.mkdir(parents=True, exist_ok=True)
        else:
            base_dir = self._ensure_dir()

        # 1. Compute derived metrics (TPS, etc.) — same as record_request_performance()
        perf = self._compute_request_performance(
            elapsed_ms=elapsed_ms,
            completion_tokens=completion_tokens,
            ttft_ms=ttft_ms,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
        )

        # 2. Build record dict with all fields
        record: Dict[str, Any] = {
            "model": model,
            "route_name": route_name or "unknown",
            "req_id": req_id,
            "stream": stream,
            **perf,
            "finish_reason": finish_reason,
            "did_summarize": did_summarize,
            "passthrough": passthrough,
            "completion_tokens_source": completion_tokens_source,
            "completed_at": event.timestamp_ns / 1_000_000_000,
        }

        if route_hierarchy is not None:
            record["route_hierarchy"] = route_hierarchy

        # ExecutionUsage fields (Phase 12) — same as record_request_performance()
        record["upstream_attempts"] = upstream_attempts
        record["usage_reported_attempts"] = usage_reported_attempts
        record["recovery_count"] = recovery_count
        record["retry_amplification_ratio"] = retry_amplification_ratio
        record["usage_complete"] = usage_complete
        record["upstream_prompt_tokens"] = upstream_prompt_tokens
        record["upstream_completion_tokens"] = upstream_completion_tokens
        record["upstream_total_tokens"] = upstream_total_tokens

        # 3. Seed in-memory stats from disk BEFORE any async I/O
        self._seed_route_stats(base_dir)

        # 4. Append to JSONL on disk (via async writer)
        filename = f"{self._safe_slug(route_name or 'unknown')}.requests.jsonl"
        path = base_dir / filename
        self._append_entry(path, record)

        # 5. Update in-memory stats (O(1))
        from ..performance import _route_stats

        rn = route_name or "unknown"
        if rn in _route_stats:
            _route_stats[rn].update(record)
        else:
            _route_stats[rn] = self._RouteStats(record)

        # 6. Periodic summary flush and archive — same as record_request_performance()
        self._request_counter += 1
        if self._request_counter == 1 or self._request_counter % self._summary_interval == 0:
            if self._request_counter > 1 and self._request_counter % 1000 == 0:
                # Archive on 1000-request boundary
                import shutil
                from datetime import datetime
                from pathlib import Path

                archive_dir = base_dir / "archive"
                archive_dir.mkdir(exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                summary_path = base_dir / "summary.yaml"
                if summary_path.exists():
                    archived = archive_dir / f"summary_{ts}.yaml"
                    shutil.move(str(summary_path), str(archived))
                for f in base_dir.glob("*.requests.jsonl"):
                    archived_file = archive_dir / f"{f.stem}_{ts}{f.suffix}"
                    shutil.move(str(f), str(archived_file))
                self._reset_route_stats()
            self._update_summary(base_dir)

    def _handle_perf_logs_dir(self, event: RuntimeEvent) -> None:
        """Handle execution.app.perf_logs_dir event.

        Updates the performance logs directory configuration at runtime.
        """
        from ..performance import set_performance_logs_dir

        message = event.data.get("message", "")
        # Extract directory path from message (format: "Performance logs directory: /path")
        if ":" in message:
            dir_path = message.split(":", 1)[1].strip()
            self._perf_logs_dir = dir_path
            set_performance_logs_dir(dir_path)

    @property
    def captured(self) -> List[RuntimeEvent]:
        """Return captured events for testing/inspection."""
        return list(self._captured)

    def clear(self) -> None:
        """Clear captured events."""
        self._captured.clear()


# ---------------------------------------------------------------------------
# LoggerConsumer
# ---------------------------------------------------------------------------


class LoggerConsumer:
    """Logger Consumer responsibility boundary.

    Implements INV-03: Logger classification, threshold filtering,
    and LogRecord projection belong to this consumer, not to producers.

    Routing (INV-03):
    - DEBUG → diagnostic logger
    - INFO → request logger
    - WARN → request logger + error logger
    - ERROR → request logger + error logger

    Output format: structured JSON with envelope fields.

    Parameters
    ----------
    capture:
        When True, store captured events in an in-memory list
        for testing/inspection. When False, only log to the
        appropriate logger.
    level:
        Minimum log level to emit (DEBUG/INFO/WARN/ERROR).
        Events below this level are filtered by the consumer.
    """

    # Logger name mapping per INV-03 routing
    _LOGGER_MAP: Dict[str, str] = {
        "DEBUG": "keeprollming.diagnostic",
        "INFO": "keeprollming.request",
        "WARN": "keeprollming.request",
        "ERROR": "keeprollming.error",
    }

    # Python logging level mapping
    _LEVEL_MAP: Dict[str, int] = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }

    def __init__(
        self,
        capture: bool = True,
        level: str = "DEBUG",
        routing: Optional[RoutingEngine] = None,
    ) -> None:
        """Initialize LoggerConsumer.

        Parameters
        ----------
        capture:
            When True, store captured events in an in-memory list
            for testing/inspection. When False, only log to the
            appropriate logger.
        level:
            Minimum log level to emit. Default "DEBUG" (all levels).
        routing:
            Optional RoutingEngine for formatter-based event routing.
            When configured, LoggerConsumer selects and applies the
            appropriate formatter before logging (Option O1).
        """
        self._capture = capture
        self._captured: List[RuntimeEvent] = []
        self._min_level = level
        self._routing = routing

        # Set up per-level loggers
        self._loggers: Dict[str, logging.Logger] = {}
        for py_level, name in self._LOGGER_MAP.items():
            lg = logging.getLogger(name)
            # Set the logger's threshold to the minimum level
            lg.setLevel(self._LEVEL_MAP.get(level, logging.DEBUG))
            # Use a simple formatter (structured JSON)
            lg.handlers.clear()
            _handler = logging.StreamHandler()
            _handler.setFormatter(_JSONFormatter())
            lg.addHandler(_handler)
            self._loggers[py_level] = lg

    def __call__(self, event: RuntimeEvent) -> None:
        """Route event to appropriate logger based on level.

        Implements INV-03: classification and threshold filtering
        are the consumer's responsibility.

        When routing is configured (Option O1), the consumer selects
        and applies the appropriate formatter before logging.

        Parameters
        ----------
        event:
            The RuntimeEvent to route.
        """
        # Capture for testing
        if self._capture:
            self._captured.append(event)

        # INV-03: threshold filtering is consumer responsibility
        configured_level = self._LEVEL_MAP.get(self._min_level, logging.DEBUG)
        event_level = self._LEVEL_MAP.get(event.level, logging.DEBUG)
        if configured_level > event_level:
            return

        # Route based on level
        logger_name = self._LOGGER_MAP.get(event.level)
        if not logger_name:
            return

        py_logger = self._loggers.get(event.level)
        if not py_logger:
            return

        # Option O1: formatter selection + formatting (Logger Consumer owns formatting)
        formatted = self._format_event(event)

        # INV-03: LogRecord projection — structured JSON output
        log_record = self._project_log_record(event)
        log_level = self._LEVEL_MAP.get(event.level, logging.INFO)
        py_logger.log(log_level, formatted, extra={"log_record": log_record})

    def _format_event(self, event: RuntimeEvent) -> str:
        """Select formatter and format the event.

        When routing is configured, delegates to RoutingEngine.
        Otherwise falls back to JsonFormatter.

        Parameters
        ----------
        event:
            The RuntimeEvent to format.

        Returns
        -------
        str
            Formatted string representation of the event.
        """
        if self._routing is not None:
            formatter = self._routing.get_formatter(event)
        else:
            formatter = JsonFormatter()
        return formatter.format(event)

    def _project_log_record(self, event: RuntimeEvent) -> dict[str, Any]:
        """Project RuntimeEvent to a structured log record.

        INV-03: LogRecord projection is the consumer's responsibility.
        INV-04: Payload fidelity preserved — no snipping/redaction.

        Parameters
        ----------
        event:
            The RuntimeEvent to project.

        Returns
        -------
        dict with structured fields for JSON output.
        """
        return {
            "type": event.type,
            "source": event.source.namespace,
            "level": event.level,
            "req_id": event.req_id,
            "data": event.data,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(event.timestamp_ns / 1e9)),
        }

    @property
    def captured(self) -> List[RuntimeEvent]:
        """Return captured events for testing/inspection."""
        return list(self._captured)

    def clear(self) -> None:
        """Clear captured events."""
        self._captured.clear()


class _JSONFormatter(logging.Formatter):
    """Simple JSON formatter for structured log output."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        log_record = getattr(record, "log_record", {})
        log_record["logger"] = record.name
        log_record["message"] = record.getMessage()
        return json.dumps(log_record, default=str)


# ---------------------------------------------------------------------------
# MetricsConsumer stub
# ---------------------------------------------------------------------------


class MetricsConsumer:
    """Metrics Consumer stub for Phase O2.

    Captures execution.* events and forwards to MetricsCollector.
    Actual MetricsCollector integration deferred to Phase O3.
    """

    def __init__(self, capture: bool = True) -> None:
        """Initialize MetricsConsumer.

        Parameters
        ----------
        capture:
            When True, store captured events in an in-memory list
            for testing/inspection.
        """
        self._capture = capture
        self._captured: List[RuntimeEvent] = []

    def __call__(self, event: RuntimeEvent) -> None:
        """Capture execution metrics event and forward to MetricsCollector.

        Parameters
        ----------
        event:
            The RuntimeEvent to capture.
        """
        if self._capture:
            self._captured.append(event)

        # Stub: in Phase O3, forward to MetricsCollector
        logger.debug(
            "[metrics] captured event: type=%s data_keys=%s",
            event.type,
            list(event.data.keys()),
        )

    @property
    def captured(self) -> List[RuntimeEvent]:
        """Return captured events for testing/inspection."""
        return list(self._captured)

    def clear(self) -> None:
        """Clear captured events."""
        self._captured.clear()
