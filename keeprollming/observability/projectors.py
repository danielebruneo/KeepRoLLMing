"""Projector model for KRM observability (D-072, Phase P1).

Defines the ``Projector`` class as a configuration-driven projection
over the EventDispatcher's event stream, plus sink abstractions.

A Projector is NOT a consumer type; it configures a subscription with
filtering (selector + level), formatting, and output routing to sinks.

Invariants:
- I-D072-01: FORMAT and LEVEL are orthogonal; no formatter may enforce
  its own event whitelist or level mapping.
- I-D072-03: The same classification decision (selector + level) applies
  before formatting; formatters receive already-filtered events.
- I-D073-01: Projector is a configuration-driven projection, not a
  consumer type.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, List, Optional

from .events import EventSource, RuntimeEvent, level_at_or_above
from .formatters import Formatter, JsonFormatter

if TYPE_CHECKING:
    from .dispatcher import EventDispatcher

logger = logging.getLogger(__name__)


# ── Sink abstraction ──────────────────────────────────────────────


class Sink(ABC):
    """Abstract sink for formatted projector output.

    A sink receives a formatted string and delivers it to an output
    destination (file, stdout, etc.). Sinks are minimal: no buffering
    or batching in Phase P1.

    Contract:
    - ``write(text)`` must not raise; errors are logged internally.
    - Sinks own their lifecycle (open/close resources).
    """

    @abstractmethod
    def write(self, text: str) -> None:
        """Write formatted text to the sink's output destination.

        Parameters
        ----------
        text:
            Formatted string from the projector's formatter.
        """
        ...


class FileSink(Sink):
    """File-based sink that appends to a specified path.

    Opens the file in append mode on first write and keeps it open
    for subsequent writes.

    Parameters
    ----------
    path:
        File path to write to. Parent directories are created if needed.
    """

    def __init__(self, path: str) -> None:
        """Initialize FileSink.

        Parameters
        ----------
        path:
            File path for output.
        """
        self._path = path
        self._file: Optional[Any] = None

    def _ensure_open(self) -> None:
        """Open the file if not already open."""
        if self._file is None:
            from pathlib import Path

            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._path, "a", encoding="utf-8")

    def write(self, text: str) -> None:
        """Append formatted text to the file.

        Silently logs errors; never raises.
        """
        try:
            self._ensure_open()
            if not text.endswith("\n"):
                text += "\n"
            self._file.write(text)
            self._file.flush()
        except Exception:
            logger.warning(
                "FileSink write error: path=%s",
                self._path,
                exc_info=True,
            )


class RotatingFileSink(FileSink):
    """Append-only file sink with bounded size and numbered retained files.

    Rotation belongs to the sink, not to a formatter or producer.  ``max_bytes``
    set to zero disables rotation; ``backup_count`` controls how many prior
    files (``.1`` through ``.N``) are retained.
    """

    def __init__(self, path: str, *, max_bytes: int = 0, backup_count: int = 0) -> None:
        super().__init__(path)
        self._max_bytes = max(0, int(max_bytes))
        self._backup_count = max(0, int(backup_count))

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        if not self._max_bytes:
            return
        try:
            current_size = os.path.getsize(self._path) if os.path.exists(self._path) else 0
            if current_size + incoming_bytes <= self._max_bytes:
                return
            if self._file is not None:
                self._file.close()
                self._file = None
            if self._backup_count:
                oldest = f"{self._path}.{self._backup_count}"
                if os.path.exists(oldest):
                    os.remove(oldest)
                for index in range(self._backup_count - 1, 0, -1):
                    source, target = f"{self._path}.{index}", f"{self._path}.{index + 1}"
                    if os.path.exists(source):
                        os.replace(source, target)
                if os.path.exists(self._path):
                    os.replace(self._path, f"{self._path}.1")
            elif os.path.exists(self._path):
                os.remove(self._path)
        except OSError:
            logger.warning("FileSink rotation error: path=%s", self._path, exc_info=True)

    def write(self, text: str) -> None:
        if not text.endswith("\n"):
            text += "\n"
        self._rotate_if_needed(len(text.encode("utf-8")))
        super().write(text)


class StdoutSink(Sink):
    """Sink that prints formatted text to stdout."""

    def write(self, text: str) -> None:
        """Print formatted text to stdout.

        Silently logs errors; never raises.
        """
        try:
            print(_colorize_stdout_plain(text))
        except Exception:
            logger.debug(
                "StdoutSink write error",
                exc_info=True,
            )


def _colorize_stdout_plain(text: str) -> str:
    """Add optional ANSI emphasis for human PLAIN output only.

    Files receive the formatter output unchanged, so they remain portable and
    grep-friendly.  The function intentionally decorates labels only; message
    content is never altered or truncated.
    """
    try:
        from ..logging.constants import (
            ANSI_BLUE,
            ANSI_BOLD,
            ANSI_BRIGHT_RED,
            ANSI_BRIGHT_YELLOW,
            ANSI_CYAN,
            ANSI_DIM,
            ANSI_GREEN,
            ANSI_MAGENTA,
            ANSI_RESET,
            ANSI_YELLOW,
            LOG_PLAIN_COLORS,
        )
        if not LOG_PLAIN_COLORS or text.lstrip().startswith("{"):
            return text
        colors = {
            "SYSTEM:": ANSI_MAGENTA,
            "USER:": ANSI_CYAN,
            "ASSISTANT:": ANSI_GREEN,
            "REASONING:": ANSI_MAGENTA,
            "TOOL_CALL:": ANSI_YELLOW,
            "TOOL_RESULT:": ANSI_YELLOW,
            "USAGE": ANSI_BOLD,
        }
        lines = []
        tool_result_payload_color = None
        for line in text.splitlines():
            stripped = line.strip()
            # Only the event line is decorated structurally: timestamp,
            # request id, event name, then key=value fields. Transcript body
            # text is deliberately left byte-for-byte readable.
            event_match = re.match(
                r"^(?P<timestamp>\S+)\s+(?P<req>\[[^]]+\])\s+(?P<event>[\w.]+)(?P<rest>.*)$",
                line,
            )
            if event_match:
                rest = re.sub(
                    r"\b([\w_]+)=([^\s]+)",
                    lambda match: (
                        f"{ANSI_YELLOW}{match.group(1)}{ANSI_RESET}="
                        f"{ANSI_GREEN}{match.group(2)}{ANSI_RESET}"
                    ),
                    event_match.group("rest"),
                )
                line = (
                    f"{ANSI_DIM}{event_match.group('timestamp')}{ANSI_RESET} "
                    f"{ANSI_CYAN}{event_match.group('req')}{ANSI_RESET} "
                    f"{ANSI_BLUE}{ANSI_BOLD}{event_match.group('event')}{ANSI_RESET}{rest}"
                )
                lines.append(line)
                tool_result_payload_color = None
                continue
            for label, color in colors.items():
                if stripped.startswith(label):
                    indent = line[:len(line) - len(line.lstrip())]
                    rest = stripped[len(label):]
                    line = f"{indent}{color}{ANSI_BOLD}{label}{ANSI_RESET}{rest}"
                    if label == "TOOL_RESULT:":
                        tool_result_payload_color = None
                    break
            # Tool metadata is deliberately on its own line, so it can be
            # distinguished from tool arguments/results without coloring the
            # payload itself.
            if stripped.startswith(("id=", "tool_call_id=")):
                indent = line[:len(line) - len(line.lstrip())]
                line = indent + re.sub(
                    r"\b([\w_]+)=([^\s]+)",
                    lambda match: (
                        f"{ANSI_YELLOW}{match.group(1)}{ANSI_RESET}="
                        f"{ANSI_MAGENTA}{match.group(2)}{ANSI_RESET}"
                    ),
                    stripped,
                )
            if stripped.startswith("stdout:"):
                indent = line[:len(line) - len(line.lstrip())]
                payload = line.lstrip()[len("stdout:"):]
                line = (
                    f"{indent}{ANSI_BRIGHT_YELLOW}{ANSI_BOLD}stdout:{ANSI_RESET}"
                    f"{ANSI_BRIGHT_YELLOW}{payload}{ANSI_RESET}"
                )
                tool_result_payload_color = ANSI_BRIGHT_YELLOW
            elif stripped.startswith("stderr:"):
                indent = line[:len(line) - len(line.lstrip())]
                payload = line.lstrip()[len("stderr:"):]
                line = (
                    f"{indent}{ANSI_BRIGHT_RED}{ANSI_BOLD}stderr:{ANSI_RESET}"
                    f"{ANSI_BRIGHT_RED}{payload}{ANSI_RESET}"
                )
                tool_result_payload_color = ANSI_BRIGHT_RED
            elif tool_result_payload_color and stripped:
                indent = line[:len(line) - len(line.lstrip())]
                line = f"{indent}{tool_result_payload_color}{line.lstrip()}{ANSI_RESET}"
            lines.append(line)
        return "\n".join(lines)
    except Exception:
        return text


# ── Projector ─────────────────────────────────────────────────────


@dataclass
class Projector:
    """Configuration-driven projection over EventDispatcher event stream.

    A Projector defines how RuntimeEvents are selected, filtered,
    formatted, and delivered to one or more sinks. It is NOT a consumer;
    it configures a subscription with filtering.

    Components (D-072 §3):
    - **selector** — event filter (prefix/glob match on event type;
      empty = all events)
    - **level** — minimum verbosity level required for emission
    - **formatter** — presentation format (e.g., JsonFormatter)
    - **sinks** — one or more output destinations

    Parameters
    ----------
    name:
        Human-readable projector name (for logging/debugging).
    selector:
        Event type filter. Empty string matches all events.
        Supports prefix matching (``streaming.*``) and glob patterns.
    level:
        Minimum event level required for emission. Events below this
        level are dropped before formatting. Default ``"INFO"``.
    formatter:
        Formatter instance used to render matching events. Defaults to
        JsonFormatter if not provided.
    sinks:
        List of Sink instances receiving formatted output. If empty,
        the projector is inactive (no output).

    Attributes
    ----------
    active:
        Whether this projector is currently subscribed to a dispatcher.
    """

    name: str
    selector: str = ""
    level: str = "INFO"
    formatter: Optional[Formatter] = None
    sinks: List[Sink] = field(default_factory=list)

    # Internal state
    active: bool = False
    _dispatcher: Optional[EventDispatcher] = field(
        default=None, repr=False, compare=False
    )
    _handler_ref: Callable[[RuntimeEvent], None] = field(
        default=None, repr=False, compare=False
    )
    _ROOT_NAMESPACES = (
        "execution",
        "request",
        "streaming",
        "routing",
        "transport",
        "downstream",
        "filter",
    )

    def __post_init__(self) -> None:
        """Validate projector configuration."""
        # Validate level
        from .events import LEVEL_ORDER

        if self.level not in LEVEL_ORDER:
            raise ValueError(
                f"Projector.level must be one of {LEVEL_ORDER}, "
                f"got: {self.level!r}"
            )

        # Default formatter
        if self.formatter is None:
            self.formatter = JsonFormatter()

        # Store stable handler reference for identity-based unsubscribe.
        # Bound methods on dataclasses create new objects each access,
        # so we cache one reference for reliable comparison in deactivate().
        self._handler_ref = self._handle_event

    def _matches_selector(self, event_type: str) -> bool:
        """Check if event type matches the projector's selector.

        Empty selector matches all events. Non-empty selector uses
        glob-style matching (fnmatch).

        Parameters
        ----------
        event_type:
            The RuntimeEvent.type to check.

        Returns
        -------
        bool
            True if event matches selector; False otherwise.
        """
        if not self.selector:
            return True
        return fnmatch.fnmatch(event_type, self.selector)

    def _should_emit(self, event: RuntimeEvent) -> bool:
        """Determine if this projector should emit the given event.

        Both selector AND level must match (conjunctive).

        Parameters
        ----------
        event:
            The RuntimeEvent to evaluate.

        Returns
        -------
        bool
            True if event passes both selector and level filters.
        """
        # Selector filter
        if not self._matches_selector(event.type):
            return False

        # Level filter
        if not level_at_or_above(event.level, self.level):
            return False

        return True

    def _handle_event(self, event: RuntimeEvent) -> None:
        """Handle a matching event: format and write to sinks.

        This is the consumer function registered with EventDispatcher.

        Parameters
        ----------
        event:
            The RuntimeEvent to process.
        """
        if not self._should_emit(event):
            return

        # Format via formatter (I-D072-03: filtering precedes formatting)
        try:
            formatted = self.formatter.format(event)
        except Exception:
            logger.debug(
                "Projector format error: name=%s event_type=%s",
                self.name,
                event.type,
                exc_info=True,
            )
            return

        # Write to all configured sinks
        for sink in self.sinks:
            try:
                sink.write(formatted)
            except Exception:
                logger.debug(
                    "Projector sink write error: name=%s sink=%s",
                    self.name,
                    type(sink).__name__,
                    exc_info=True,
                )

    def _subscription_namespaces(self) -> tuple[str, ...]:
        """Return the dispatcher namespaces needed by this projection."""
        return (self.name, *self._ROOT_NAMESPACES)

    def activate(self, dispatcher: EventDispatcher) -> None:
        """Subscribe this projector to an EventDispatcher.

        Registers the projector's event handler as a consumer with the
        dispatcher. The projector subscribes to all root namespaces so
        it can apply its own selector and level filtering.

        Parameters
        ----------
        dispatcher:
            The EventDispatcher to subscribe to.

        Raises
        ------
        RuntimeError
            If already active on a different dispatcher.
        """
        if self.active and self._dispatcher is not None:
            if self._dispatcher is not dispatcher:
                raise RuntimeError(
                    f"Projector {self.name!r} is already active on a "
                    f"different dispatcher"
                )
            return

        # Subscribe to all root namespaces so we can apply our own filtering.
        # We subscribe broadly and filter locally via _should_emit().
        # This keeps the subscription model simple: one projector = one
        # consumer function, regardless of selector pattern.
        handler = self._handler_ref
        # Subscribe broadly and filter locally via _should_emit().
        for ns in self._subscription_namespaces():
            dispatcher.subscribe(ns, handler)

        self._dispatcher = dispatcher
        self.active = True

    def deactivate(self) -> None:
        """Unsubscribe this projector from its EventDispatcher.

        Removes the projector's consumer registration. After deactivation,
        the projector receives no events until reactivated.
        """
        if not self.active or self._dispatcher is None:
            return

        # Remove our handler from all subscribed namespaces.
        # Use _handler_ref for identity comparison (bound methods on
        # dataclasses create new objects each access).
        handler = self._handler_ref
        for ns in self._subscription_namespaces():
            if ns in self._dispatcher._consumers:
                self._dispatcher._consumers[ns] = [
                    fn for fn in self._dispatcher._consumers[ns]
                    if fn is not handler
                ]
                # Clean up empty lists
                if not self._dispatcher._consumers[ns]:
                    del self._dispatcher._consumers[ns]

        self._dispatcher = None
        self.active = False


class QueuedProjector:
    """Run one optional projection through a bounded FIFO worker.

    Formatting and sink writes are presentation work, not request-path work.
    This adapter keeps the synchronous dispatcher callback constant-time while
    preserving event order for an individual projector.  An overloaded queue
    drops only that optional projection and publishes a single structured
    overflow event until the queue makes progress again.
    """

    def __init__(self, projector: Projector, *, max_queue_size: int = 2048) -> None:
        self.projector = projector
        self.max_queue_size = max(1, int(max_queue_size))
        self._queue: asyncio.Queue[RuntimeEvent] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._dispatcher: EventDispatcher | None = None
        self._active = False
        self._dropped_events = 0
        self._overflow_notice_pending = False
        self._handler_ref = self._enqueue

    @property
    def active(self) -> bool:
        """Whether the bounded worker is subscribed and accepting events."""
        return self._active

    @property
    def dropped_events(self) -> int:
        """Number of optional projection events rejected due to queue pressure."""
        return self._dropped_events

    @property
    def queued_events(self) -> int:
        """Current pending work, useful for operational inspection and tests."""
        return self._queue.qsize() if self._queue is not None else 0

    async def start(self, dispatcher: EventDispatcher) -> None:
        """Subscribe the projector and start its single ordered worker."""
        if self._active:
            if self._dispatcher is not dispatcher:
                raise RuntimeError(
                    f"Queued projector {self.projector.name!r} is already active "
                    "on a different dispatcher"
                )
            return

        self._dispatcher = dispatcher
        self._queue = asyncio.Queue(maxsize=self.max_queue_size)
        for namespace in self.projector._subscription_namespaces():
            dispatcher.subscribe(namespace, self._handler_ref)
        self._worker = asyncio.create_task(
            self._run(),
            name=f"krm-projector-{self.projector.name}",
        )
        self._active = True

    def _enqueue(self, event: RuntimeEvent) -> None:
        """Accept a matching event without formatting or performing I/O."""
        if (
            event.type == "execution.observability.projection_overflow"
            and event.data.get("projector") == self.projector.name
        ):
            # The queue that overflowed cannot reliably retain its own notice;
            # do not count that diagnostic event as another lost payload.
            return
        if not self.projector._should_emit(event):
            return
        queue = self._queue
        if queue is None:
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_events += 1
            if not self._overflow_notice_pending:
                self._overflow_notice_pending = True
                self._emit_overflow_event()

    def _emit_overflow_event(self) -> None:
        """Make optional-observability loss visible to other projections."""
        if self._dispatcher is None:
            return
        self._dispatcher.emit(
            RuntimeEvent(
                type="execution.observability.projection_overflow",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="execution", component="observability"),
                data={
                    "projector": self.projector.name,
                    "queue_max_events": self.max_queue_size,
                    "dropped_events": self._dropped_events,
                },
                level="WARN",
            )
        )

    async def _run(self) -> None:
        """Serialize blocking formatting and sink delivery off the event loop."""
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            try:
                await asyncio.to_thread(self.projector._handle_event, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "Queued projector worker error: name=%s event_type=%s",
                    self.projector.name,
                    event.type,
                    exc_info=True,
                )
            finally:
                self._queue.task_done()
                if self._queue.qsize() < self.max_queue_size:
                    self._overflow_notice_pending = False

    async def wait_idle(self) -> None:
        """Wait until all events accepted so far have reached the sink."""
        if self._queue is not None:
            await self._queue.join()

    async def stop(self, *, drain_timeout: float = 2.0) -> None:
        """Unsubscribe and drain pending optional output within a bounded time."""
        if not self._active:
            return
        dispatcher = self._dispatcher
        if dispatcher is not None:
            for namespace in self.projector._subscription_namespaces():
                consumers = dispatcher._consumers.get(namespace)
                if consumers is None:
                    continue
                dispatcher._consumers[namespace] = [
                    fn for fn in consumers if fn is not self._handler_ref
                ]
                if not dispatcher._consumers[namespace]:
                    del dispatcher._consumers[namespace]
        self._active = False
        try:
            await asyncio.wait_for(self.wait_idle(), timeout=drain_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Queued projector shutdown timeout: name=%s queued_events=%s",
                self.projector.name,
                self.queued_events,
            )
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None
        self._queue = None
        self._dispatcher = None
