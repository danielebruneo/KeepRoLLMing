"""Opt-in exact-byte trace persistence for streaming diagnosis and replay."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import RuntimeEvent


class RawTraceConsumer:
    """Persist selected transport events without routing their bytes to JSONL.

    The trace is intentionally separate from normal logs.  Each retained record
    carries a request-local sequence, monotonic timing and base64-encoded raw
    bytes so a later replay driver can reproduce exact SSE boundaries.
    """

    def __init__(
        self,
        *,
        policy: str = "disabled",
        selected_routes: list[str] | None = None,
        base_dir: str | Path | None = None,
        max_bytes_per_request: int = 20 * 1024 * 1024,
    ) -> None:
        if policy not in {"disabled", "all", "selected_routes"}:
            raise ValueError("raw trace policy must be disabled, all, or selected_routes")
        self._policy = policy
        self._selected_routes = set(selected_routes or [])
        self._base_dir = Path(base_dir or "__raw_traces__")
        self._max_bytes = max(0, int(max_bytes_per_request))
        self._accepted: set[str] = set()
        self._sequence: dict[str, int] = {}
        self._written: dict[str, int] = {}
        self._truncated: set[str] = set()
        # ASGI response-start precedes the lazy body iterator that announces
        # ``request_started``.  Retain a few metadata-only facts until the
        # route is known, then either persist or discard them deterministically.
        self._pending_lifecycle: dict[str, list[RuntimeEvent]] = {}

    def __call__(self, event: RuntimeEvent) -> None:
        if self._policy == "disabled" or not event.req_id:
            return
        if event.type == "transport.trace.request_started":
            self._accept_request(event)
            return
        if event.req_id not in self._accepted:
            if event.type == "transport.trace.lifecycle":
                pending = self._pending_lifecycle.setdefault(event.req_id, [])
                if len(pending) < 8:
                    pending.append(event)
            return
        if event.type == "transport.trace.chunk":
            self._write_chunk(event)
        elif event.type == "transport.trace.lifecycle":
            self._write_lifecycle(event)

    def _accept_request(self, event: RuntimeEvent) -> None:
        route = str((event.data or {}).get("route", ""))
        if self._policy == "all" or route in self._selected_routes:
            self._accepted.add(event.req_id)
            self._sequence[event.req_id] = 0
            self._written[event.req_id] = 0
            for pending_event in self._pending_lifecycle.pop(event.req_id, []):
                self._write_lifecycle(pending_event)
        else:
            self._pending_lifecycle.pop(event.req_id, None)

    def _write_chunk(self, event: RuntimeEvent) -> None:
        raw = (event.data or {}).get("raw_bytes")
        if not isinstance(raw, bytes):
            return
        req_id = event.req_id
        if self._written[req_id] + len(raw) > self._max_bytes:
            if req_id not in self._truncated:
                self._truncated.add(req_id)
                self._write_record(req_id, {"kind": "truncated", "max_bytes": self._max_bytes})
            return
        data = event.data or {}
        self._sequence[req_id] += 1
        record = {
            "format_version": 1,
            "sequence": self._sequence[req_id],
            "req_id": req_id,
            "direction": data.get("direction"),
            "boundary": data.get("boundary"),
            "chunk_index": data.get("chunk_index"),
            "monotonic_ns": data.get("monotonic_ns"),
            "relative_ns": data.get("relative_ns"),
            "bytes_b64": base64.b64encode(raw).decode("ascii"),
        }
        self._write_record(req_id, record)
        self._written[req_id] += len(raw)

    def _write_lifecycle(self, event: RuntimeEvent) -> None:
        """Write metadata-only transport facts in their observed order."""
        req_id = event.req_id
        self._sequence[req_id] += 1
        data = event.data or {}
        self._write_record(req_id, {
            "format_version": 1,
            "sequence": self._sequence[req_id],
            "req_id": req_id,
            "kind": "lifecycle",
            "boundary": data.get("boundary"),
            "monotonic_ns": data.get("monotonic_ns"),
            "timestamp_ns": event.timestamp_ns,
            "data": {
                key: value
                for key, value in data.items()
                if key not in {"boundary", "monotonic_ns", "raw_bytes"}
            },
        })

    def _write_record(self, req_id: str, record: dict[str, Any]) -> None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self._base_dir / date / req_id / "trace.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            pass
