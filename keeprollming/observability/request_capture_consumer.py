"""Request Capture Consumer — raw request capture (Phase O12).

Subscribes to request.capture.raw_inbound events and persists the full
upstream payload plus correlation metadata for debugging and future
replay compatibility. Follows BodyCaptureConsumer architectural pattern.

Invariants:
- Non-blocking: persistence failures are logged but never propagate into
  the request lifecycle.
- Max body size: 50MB per file; truncate with metadata if exceeded.
- Storage format: per-request directory with boundary-labeled JSON files.
- Redactor interface allows future PII/API key filtering without changing
  event emission or consumer structure.
- Capture failure is isolated; consumer exceptions never propagate into
  request lifecycle (I-O12-3).

Event subscriptions:
- request.capture.raw_inbound — raw request capture events

Storage layout:
    __request_captures__/
    ├── 2026-07-21/
    │   ├── abc123/
    │   │   ├── meta.json                   # correlation metadata
    │   │   └── request.raw_inbound.json    # full raw_body JSON
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .events import RuntimeEvent
from .redactor import NoOpRedactor, Redactor


# Max file size per capture (50MB)
_MAX_BODY_SIZE_BYTES = 50 * 1024 * 1024

# Capture format version for future evolution
_CAPTURE_FORMAT_VERSION = "1.0"


class RequestCaptureConsumer:
    """Request Capture Consumer — raw request persistence.

    Captures the effective upstream payload (post-route-resolution,
    pre-filter-chain) with correlation metadata. Persists to
    ``__request_captures__/{date}/{req_id}/`` with boundary-labeled
    JSON files.

    Parameters
    ----------
    base_dir : str | Path
        Root directory for request captures. Default: project root /__request_captures__/
    policy : str
        Capture policy: ``disabled``, ``all``, or ``selected_routes``.
        Default: ``disabled`` (production safety).
    selected_routes : list[str] | None
        When policy is ``selected_routes``, only capture requests for these routes.
    redactor : Redactor
        Optional redaction interface applied before persistence.
        Default is NoOpRedactor (no redaction).
    """

    # Events subscribed for raw request capture
    CAPTURE_EVENTS = {
        "request.capture.raw_inbound",
    }

    def __init__(
        self,
        base_dir: Optional[str | Path] = None,
        policy: str = "disabled",
        selected_routes: Optional[list[str]] = None,
        redactor: Optional[Redactor] = None,
    ) -> None:
        """Initialize RequestCaptureConsumer.

        Parameters
        ----------
        base_dir : str | Path | None
            Root directory for request captures. If None, uses project root
            /__request_captures__/ (relative to keeprollming package).
        policy : str
            Capture policy: ``disabled``, ``all``, or ``selected_routes``.
            Default: ``disabled`` (production safety).
        selected_routes : list[str] | None
            Routes to capture when policy is ``selected_routes``.
        redactor : Redactor | None
            Redaction interface. Default: NoOpRedactor.
        """
        self._base_dir = Path(base_dir) if base_dir is not None else self._default_base_dir()
        self._policy = policy
        self._selected_routes = set(selected_routes or [])
        self._redactor = redactor or NoOpRedactor()
        self._ensure_base_dir()

    def _default_base_dir(self) -> Path:
        """Compute default capture directory at project root."""
        # Go up from keeprollming/observability/ to project root
        pkg_root = Path(__file__).resolve().parent.parent.parent
        return pkg_root / "__request_captures__"

    def _ensure_base_dir(self) -> None:
        """Create base directory if it doesn't exist."""
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # If we can't create the directory, captures will fail silently
            pass

    def __call__(self, event: RuntimeEvent) -> None:
        """Process a capture event and persist if policy matches.

        Parameters
        ----------
        event : RuntimeEvent
            The capture event to process.
        """
        # Disabled policy — no captures
        if self._policy == "disabled":
            return

        # Only handle capture events
        if event.type not in self.CAPTURE_EVENTS:
            return

        # selected_routes policy — check route match
        if self._policy == "selected_routes":
            route = event.data.get("resolved_route")
            if not route or route not in self._selected_routes:
                return

        # all policy or matched selected_routes — capture
        self._capture_request(event)

    def _capture_request(self, event: RuntimeEvent) -> None:
        """Capture raw request body and metadata.

        Persists to ``__request_captures__/{date}/{req_id}/`` with boundary-labeled
        JSON files. All I/O is wrapped in try/except to ensure non-blocking.

        Parameters
        ----------
        event : RuntimeEvent
            The capture event containing raw_body and correlation metadata.
        """
        try:
            req_id = event.req_id
            if not req_id:
                # No req_id — cannot create capture directory; skip silently
                return

            data = event.data
            raw_body = data.get("raw_body")
            if raw_body is None:
                # No raw body to capture; skip silently
                return

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            capture_dir = self._base_dir / date_str / self._sanitize_req_id(req_id)

            try:
                capture_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return

            # 1. meta.json — correlation metadata with versioning
            meta = {
                "capture_format_version": _CAPTURE_FORMAT_VERSION,
                "req_id": req_id,
                "policy": self._policy,
                "trigger_event": event.type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "unix_ts": time.time(),
            }

            # Add correlation metadata from event data
            if "client_model" in data:
                meta["client_model"] = data["client_model"]
            if "resolved_route" in data:
                meta["resolved_route"] = data["resolved_route"]
            if "upstream_model" in data:
                meta["upstream_model"] = data["upstream_model"]
            if "upstream_url" in data:
                meta["upstream_url"] = data["upstream_url"]
            if "route_hierarchy" in data:
                meta["route_hierarchy"] = data["route_hierarchy"]

            self._write_json(capture_dir / "meta.json", meta)

            # 2. request.raw_inbound.json — full raw_body (redacted if configured)
            redacted_body = self._redactor.redact(raw_body)
            self._write_json(capture_dir / "request.raw_inbound.json", redacted_body)

        except Exception:
            # Consumer failure isolation — never propagate into request lifecycle
            pass

    def _write_json(self, path: Path, data: Any) -> None:
        """Write JSON to file with max body size enforcement.

        If serialized data exceeds ``_MAX_BODY_SIZE_BYTES``, writes a summary
        with truncation metadata instead of the full payload.

        Parameters
        ----------
        path : Path
            Target file path.
        data : Any
            Data to serialize and write.
        """
        try:
            payload_bytes = json.dumps(data, indent=2, default=str).encode("utf-8")

            if len(payload_bytes) > _MAX_BODY_SIZE_BYTES:
                # Truncate — write summary with metadata
                summary = self._make_truncation_summary(data, len(payload_bytes))
                payload_bytes = json.dumps(summary, indent=2, default=str).encode("utf-8")

            path.write_bytes(payload_bytes)
        except (OSError, TypeError, ValueError):
            # Persistence failure — log silently, don't propagate
            pass

    def _make_truncation_summary(self, data: Any, original_size: int) -> Dict[str, Any]:
        """Create a summary when data exceeds max body size.

        Parameters
        ----------
        data : Any
            The original data that was too large.
        original_size : int
            Size in bytes of the full serialized payload.

        Returns
        -------
        dict
            Summary with truncation metadata.
        """
        summary: Dict[str, Any] = {
            "_truncated": True,
            "_reason": f"Payload exceeded {_MAX_BODY_SIZE_BYTES // (1024 * 1024)}MB limit",
            "_original_size_bytes": original_size,
        }

        # Add structural metadata if data is a dict
        if isinstance(data, dict):
            summary["_keys"] = list(data.keys())
        elif isinstance(data, list):
            summary["_length"] = len(data)

        return summary

    @staticmethod
    def _sanitize_req_id(req_id: str) -> str:
        """Sanitize req_id for use in directory names.

        Parameters
        ----------
        req_id : str
            Request ID to sanitize.

        Returns
        -------
        str
            Safe directory name derived from req_id.
        """
        # Replace unsafe characters with underscores
        sanitized = "".join(c if c.isalnum() or c in "-_." else "_" for c in req_id)
        # Trim if too long
        if len(sanitized) > 100:
            sanitized = sanitized[:80] + "..." + sanitized[-15:]
        return sanitized
