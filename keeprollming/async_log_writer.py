"""Async batched log writer — eliminates sync I/O blocking in the hot path.

Replaces synchronous file.write()+flush() with an asyncio.Queue drained by a
background writer task.  Supports multiple named sinks (NDJSON log, filter logs,
debug logs, performance logs) through a single shared writer loop.

If a sink produces I/O errors (disk full, permissions, NFS outage), the writer
logs the failure on first occurrence and marks the sink dead after 3 consecutive
failures.  Callers can check ``is_sink_dead()`` to fall back to synchronous
writes.  A single successful write revives all dead sinks (optimistic recovery).

Usage:
    writer = AsyncLogWriter()
    await writer.start()
    writer.enqueue("json_log", {"ts": 1.0, "msg": "hello"})   # from sync ctx
    await writer.enqueue_async("json_log", {"ts": 2.0, ...})  # from async ctx
    await writer.stop()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Max consecutive failures before a sink is marked dead.
_MAX_SINK_FAILURES = 3


# ── Per-sink metadata ──────────────────────────────────────────────

class _Sink:
    __slots__ = ("path", "fh", "mode", "last_flush")
    path: str
    fh: Any          # file object
    mode: str        # "a" for append-only, "w" for writers that manage their own lifecycle
    last_flush: float

    def __init__(self, path: str, mode: str = "a") -> None:
        self.path = path
        self.fh = None
        self.mode = mode
        self.last_flush = 0.0


# ── Main writer ────────────────────────────────────────────────────

class AsyncLogWriter:
    """Background writer that batches log records and flushes periodically.

    Thread-safe: can be called from sync code via ``enqueue()`` which uses
    ``call_soon_threadsafe``, or from async code via ``enqueue_async()``.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=10000)
        self._task: asyncio.Task[None] | None = None
        self._sinks: Dict[str, _Sink] = {}
        self._flush_interval: float = 1.0     # seconds between forced flushes
        self._running: bool = False
        # ── Failure tracking (BUG 2 fix) ──
        self._sink_failures: Dict[str, int] = {}   # sink_name → consecutive failures

    # ── Sink management ────────────────────────────────────────

    def register_sink(self, name: str, path: str, mode: str = "a") -> None:
        """Register a named log file destination.

        ``path`` may be relative (resolved against cwd at first write).
        """
        self._sinks[name] = _Sink(path, mode)
        # Reset failure count when re-registering
        self._sink_failures.pop(name, None)

    def is_sink_dead(self, sink_name: str) -> bool:
        """Return True if the sink has had ``_MAX_SINK_FAILURES`` consecutive I/O errors.

        Callers should fall back to synchronous writes when this returns True.
        """
        return self._sink_failures.get(sink_name, 0) >= _MAX_SINK_FAILURES

    # ── Lifecycle ──────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background writer task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._writer_loop())

    async def stop(self) -> None:
        """Stop the writer, flush all pending records, close all file handles."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.flush()
        # Close all sinks
        for sink in self._sinks.values():
            if sink.fh is not None:
                try:
                    sink.fh.close()
                except Exception:
                    pass
                sink.fh = None

    async def flush(self) -> None:
        """Drain the queue and flush all file handles to disk.

        Call this before reading log files in tests or before shutdown.
        """
        # Drain all items currently in the queue
        batch: list[tuple[str, str]] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                batch.append(item)
            except asyncio.QueueEmpty:
                break
        if batch:
            self._flush_batch(batch)
        # Flush file handles
        for sink in self._sinks.values():
            if sink.fh is not None:
                try:
                    sink.fh.flush()
                except Exception:
                    pass

    # ── Public enqueue ─────────────────────────────────────────

    def enqueue(self, sink_name: str, record: dict) -> None:
        """Enqueue a JSON-serialisable record for writing (sync-safe).

        Called from synchronous code.  Uses ``call_soon_threadsafe`` to safely
        push onto the asyncio queue from outside the event loop thread.
        """
        try:
            line = json.dumps(record, default=str, separators=(",", ":"))
        except Exception:
            return  # never let serialisation throw
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._queue.put_nowait, (sink_name, line))
        except RuntimeError:
            # No running event loop — fall back to immediate sync write.
            # This path should be rare (tests, early startup).
            self._sync_write(sink_name, line)

    async def enqueue_async(self, sink_name: str, record: dict) -> None:
        """Enqueue a record from an async context."""
        try:
            line = json.dumps(record, default=str, separators=(",", ":"))
        except Exception:
            return
        await self._queue.put((sink_name, line))

    # ── Internals ──────────────────────────────────────────────

    def _ensure_fh(self, sink: _Sink) -> None:
        if sink.fh is not None:
            # If the file was externally unlinked (e.g. dashboard reset_logs()),
            # close the orphaned handle so a new file is created on next write.
            if not os.path.exists(sink.path):
                try:
                    sink.fh.close()
                except OSError:
                    pass
                sink.fh = None
            else:
                return
        os.makedirs(os.path.dirname(sink.path) or ".", exist_ok=True)
        sink.fh = open(sink.path, sink.mode, encoding="utf-8")

    def _sync_write(self, sink_name: str, line: str) -> None:
        """Fallback: synchronous immediate write + flush when no loop."""
        sink = self._sinks.get(sink_name)
        if sink is None:
            return
        try:
            self._ensure_fh(sink)
            sink.fh.write(line + "\n")
            sink.fh.flush()
        except Exception:
            pass

    async def _writer_loop(self) -> None:
        """Drain the queue and batch-write to sinks."""
        batch: list[tuple[str, str]] = []
        last_flush = time.monotonic()

        while self._running:
            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=self._flush_interval,
                )
                batch.append(item)

                if len(batch) >= 100:  # batch size threshold
                    self._flush_batch(batch)
                    batch.clear()
                    last_flush = time.monotonic()

            except asyncio.TimeoutError:
                # Periodic flush
                now = time.monotonic()
                if batch and (now - last_flush >= self._flush_interval):
                    self._flush_batch(batch)
                    batch.clear()
                    last_flush = now

        # Drain remaining items on shutdown
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                batch.append(item)
            except asyncio.QueueEmpty:
                break
        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list[tuple[str, str]]) -> None:
        """Write a batch of (sink_name, json_line) tuples to their files.

        Tracks I/O errors per sink.  After ``_MAX_SINK_FAILURES`` consecutive
        failures a sink is marked dead — callers should switch to sync writes.
        A single successful write revives all previously dead sinks (optimistic
        recovery for transient errors like NFS remount or disk space freed).
        """
        # Group by sink for efficiency
        by_sink: dict[str, list[str]] = {}
        for sink_name, line in batch:
            by_sink.setdefault(sink_name, []).append(line)

        any_success = False

        for sink_name, lines in by_sink.items():
            sink = self._sinks.get(sink_name)
            if sink is None:
                continue
            try:
                self._ensure_fh(sink)
                for line in lines:
                    sink.fh.write(line + "\n")
                sink.fh.flush()
                # Success — clear failures for this sink
                self._sink_failures.pop(sink_name, None)
                any_success = True
            except Exception as exc:
                count = self._sink_failures.get(sink_name, 0) + 1
                self._sink_failures[sink_name] = count
                if count == 1:
                    logger.warning(
                        "AsyncLogWriter: sink '%s' write failed (%s) — "
                        "will retry, then fall back to sync after %d consecutive errors",
                        sink_name, exc, _MAX_SINK_FAILURES,
                    )
                elif count >= _MAX_SINK_FAILURES:
                    logger.error(
                        "AsyncLogWriter: sink '%s' marked dead after %d consecutive "
                        "write failures. Last error: %s. Callers falling back to sync I/O.",
                        sink_name, count, exc,
                    )

        # Optimistic recovery: a single success revives all dead sinks.
        if any_success and self._sink_failures:
            revived = list(self._sink_failures.keys())
            self._sink_failures.clear()
            logger.info(
                "AsyncLogWriter: revived %d previously dead sink(s): %s",
                len(revived), revived,
            )


# ── Singleton ──────────────────────────────────────────────────────

_async_writer: AsyncLogWriter | None = None


def get_async_writer() -> AsyncLogWriter:
    """Return (and lazily create) the global AsyncLogWriter singleton."""
    global _async_writer
    if _async_writer is None:
        _async_writer = AsyncLogWriter()
    return _async_writer


async def start_async_writer() -> None:
    """Start the global writer and register default sinks.

    Called from the FastAPI lifespan startup.
    """
    writer = get_async_writer()

    import os as _os
    log_dir = _os.environ.get("LOG_PATH", ".")
    json_path = _os.path.join(log_dir, "keeprollming.log.json")

    writer.register_sink("json_log", json_path)
    await writer.start()


async def stop_async_writer() -> None:
    """Stop the global writer.  Called from the FastAPI lifespan shutdown."""
    global _async_writer
    if _async_writer is not None:
        await _async_writer.stop()
        _async_writer = None
