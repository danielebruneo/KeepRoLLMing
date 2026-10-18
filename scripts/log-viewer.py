#!/usr/bin/env python3
"""Render current KRM JSONL events with correlated original-route/backend filters."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from keeprollming.observability.events import EventSource, RuntimeEvent  # noqa: E402
from keeprollming.observability.formatters import FORMAT_ERROR, PlainTextFormatter  # noqa: E402

FORMATTER = PlainTextFormatter()


def decode_record(line: str) -> dict:
    record = json.loads(line)
    if not isinstance(record, dict) or not isinstance(record.get("type"), str) or not record["type"]:
        raise ValueError("expected a current KRM event with a type field")
    if not isinstance(record.get("data", {}), dict):
        raise ValueError("event data must be an object")
    timestamp = record.get("timestamp_ms", 0)
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
        raise ValueError("timestamp_ms must be a finite number")
    if record.get("req_id") is not None and not isinstance(record["req_id"], str):
        raise ValueError("req_id must be a string or null")
    return record


def render_plain(record: dict) -> str:
    """Use the server formatter; never silently discard formatting failures."""
    source = record.get("source") or {}
    if not isinstance(source, dict):
        source = {}
    try:
        event = RuntimeEvent(
            type=record["type"],
            source=EventSource(
                source.get("domain", "viewer"), source.get("component", "event"), source.get("instance")
            ),
            timestamp_ns=int(record.get("timestamp_ms", 0) * 1_000_000),
            req_id=record.get("req_id"),
            level=record.get("level", "INFO"),
            trace_id=record.get("trace_id"),
            span_id=record.get("span_id"),
            data=record.get("data", {}),
        )
        rendered = FORMATTER.format(event)
        if rendered != FORMAT_ERROR:
            return rendered
    except (TypeError, ValueError, KeyError, OverflowError):
        pass
    return f"{FORMAT_ERROR} {json.dumps(record, ensure_ascii=False)}"


def matches(patterns, values) -> bool:
    return not patterns or any(fnmatchcase(value, pattern) for value in values for pattern in patterns)


@dataclass
class RequestInfo:
    routes: set[str] = field(default_factory=set)
    backends: set[str] = field(default_factory=set)
    selected: bool = False

    def observe(self, record: dict) -> None:
        data, kind = record.get("data", {}), record["type"]

        def collect(destination, *keys):
            destination.update(data[key] for key in keys if isinstance(data.get(key), str) and data[key])
            for key in keys:
                value = data.get(key)
                if isinstance(value, str) and (key == "url" or key.endswith("_url")):
                    value = value.rstrip("/")
                    destination.add(value)
                    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1/embeddings"):
                        if value.endswith(suffix):
                            destination.add(value[: -len(suffix)])

        # Never infer the original route from a selected target or retry route.
        collect(self.routes, "requested_route", "client_model")
        if kind in ("execution.chat.route_resolved", "routing.resolution.resolved"):
            collect(self.backends, "resolved_route", "upstream_model", "upstream_url")
        elif kind == "execution.routing.balance_decision":
            collect(self.backends, "selected_route", "selected_model", "selected_upstream_url")
        elif kind == "execution.chat.fallback":
            collect(
                self.backends,
                "from_route",
                "from_model",
                "from_upstream_url",
                "to_route",
                "to_model",
                "to_upstream_url",
            )
        elif kind == "execution.chat.request_route":
            collect(self.backends, "route")
        elif kind == "execution.chat.repacked":
            collect(self.backends, "upstream_url")
        elif kind in (
            "execution.chat.upstream_error",
            "execution.upstream.response_received",
            "execution.upstream.connection_error",
        ):
            collect(self.backends, "route", "upstream_model", "upstream_url", "url")
        # Exclude fallback_chain and alternatives: configured != used.


class RequestFilter:
    """Bounded correlation, retaining early events until route/backend is known.

    Backend matches latch for the request, so later fallback events stay visible.
    Delayed records retain their timestamps and their within-request order.
    """

    def __init__(
        self,
        routes=(),
        backends=(),
        events=(),
        *,
        req_ids=(),
        max_requests=10000,
        max_pending=2048,
        max_bytes=16 * 1024 * 1024,
    ):
        self.routes, self.backends, self.events = list(routes), list(backends), list(events)
        self.req_ids = list(req_ids)
        self.requests: OrderedDict[str, RequestInfo] = OrderedDict()
        self.pending = deque()
        self.pending_bytes = 0
        self.max_requests, self.max_pending, self.max_bytes = max_requests, max_pending, max_bytes
        self.dropped = 0

    def info(self, record: dict) -> RequestInfo | None:
        req_id = record.get("req_id")
        if not req_id or not matches(self.req_ids, [req_id]):
            return None
        if req_id not in self.requests:
            self.requests[req_id] = RequestInfo()
        self.requests.move_to_end(req_id)
        while len(self.requests) > self.max_requests:
            evicted, _ = self.requests.popitem(last=False)
            self._remove_pending(evicted)
        state = self.requests[req_id]
        state.observe(record)
        if matches(self.routes, state.routes) and matches(self.backends, state.backends):
            state.selected = True
        return state

    def _remove_pending(self, req_id, *, release=False):
        kept, out = deque(), []
        for record, size in self.pending:
            if record.get("req_id") == req_id:
                self.pending_bytes -= size
                if release:
                    out.append(record)
                else:
                    self.dropped += 1
            else:
                kept.append((record, size))
        self.pending = kept
        return out

    def feed(self, record: dict, *, size: int = 0) -> list[dict]:
        if self.req_ids and (not record.get("req_id") or not matches(self.req_ids, [record["req_id"]])):
            return []
        state = self.info(record)  # Learn metadata even when --filter-event hides it.
        event_matches = matches(self.events, [record["type"]])
        if not self.routes and not self.backends:
            return [record] if event_matches else []
        if state is None:
            return []
        if state.selected:
            out = self._remove_pending(record["req_id"], release=True)
            if event_matches:
                out.append(record)
            return out
        if event_matches:
            size = size or len(json.dumps(record).encode("utf-8"))
            self.pending.append((record, size))
            self.pending_bytes += size
            while len(self.pending) > self.max_pending or self.pending_bytes > self.max_bytes:
                _, old_size = self.pending.popleft()
                self.pending_bytes -= old_size
                self.dropped += 1
        return []


def read_lines(path: Path, *, follow=False, poll_interval=0.1):
    """Read from the start; follow replacement/truncation and retain partial lines."""
    stream, fragment = None, b""
    try:
        while True:
            if stream is None:
                try:
                    stream = path.open("rb")
                except FileNotFoundError:
                    if not follow:
                        raise
                    time.sleep(poll_interval)
                    continue
            part = stream.readline()
            if part:
                fragment += part
                if fragment.endswith(b"\n"):
                    yield fragment.decode("utf-8", errors="replace")
                    fragment = b""
                continue
            if not follow:
                if fragment:
                    yield fragment.decode("utf-8", errors="replace")
                return
            try:
                current, opened = path.stat(), os.fstat(stream.fileno())
                replaced = (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                truncated = current.st_size < stream.tell()
            except FileNotFoundError:
                replaced = truncated = False
            if replaced or truncated:
                if fragment.strip():
                    print("log-viewer: discarded incomplete line at rotation/truncation", file=sys.stderr)
                stream.close()
                stream, fragment = None, b""
                continue
            time.sleep(poll_interval)
    finally:
        if stream is not None:
            stream.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", "-f", default="keeprollming.log.json", help="KRM JSONL file (not PLAIN/raw capture)")
    parser.add_argument("--tail", "-t", action="store_true", help="Follow new records, including file rotation")
    parser.add_argument("--plain-output", "-o", help="Write rendered text to this file")
    parser.add_argument("--filter-event", action="append", default=[], help="Event type or glob; repeat for OR")
    parser.add_argument(
        "--req-id", "--filter-req-id", action="append", default=[], help="Request ID or glob; repeat for OR"
    )
    parser.add_argument(
        "--route", "--filter-route", action="append", default=[], help="Original requested route/client model or glob"
    )
    parser.add_argument(
        "--backend",
        "--filter-backend",
        action="append",
        default=[],
        help="Resolved target route, upstream model or URL (glob allowed)",
    )
    args = parser.parse_args(argv)
    path = Path(args.file).expanduser()
    if not path.is_absolute():
        path = Path(os.environ.get("LOG_PATH", ".")) / path
    if not path.is_file():
        parser.error(f"log file not found: {path}")
    output_path = Path(args.plain_output).expanduser() if args.plain_output else None
    if output_path and (
        output_path.resolve() == path.resolve() or (output_path.exists() and os.path.samefile(output_path, path))
    ):
        parser.error("output must not overwrite the input log")
    correlator = RequestFilter(args.route, args.backend, args.filter_event, req_ids=args.req_id)
    output = None
    warned = False
    try:
        output = output_path.open("w", encoding="utf-8") if output_path else sys.stdout
        # Warm identity from existing records without replaying them in --tail.
        initial_size = path.stat().st_size if args.tail else 0
        consumed = 0
        for line in read_lines(path, follow=args.tail):
            consumed += len(line.encode("utf-8"))
            if not line.strip():
                continue
            try:
                record = decode_record(line)
            except (ValueError, TypeError) as exc:
                print(f"log-viewer: skipped invalid record: {exc}", file=sys.stderr)
                continue
            if args.tail and consumed <= initial_size:
                correlator.info(record)
                continue
            for selected in correlator.feed(record, size=len(line.encode("utf-8"))):
                output.write(render_plain(selected) + "\n")
                output.flush()
            if correlator.dropped and not warned:
                print(
                    "log-viewer: correlation buffer limit reached; older unmatched events were dropped", file=sys.stderr
                )
                warned = True
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except OSError as exc:
        print(f"log-viewer: {exc}", file=sys.stderr)
        return 1
    finally:
        if output_path and output is not None:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
