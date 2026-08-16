"""Body Capture Consumer — high-fidelity error payload capture (Phase O11).

Subscribes to error events and persists request/response bodies to disk
for debugging upstream failures. Implements errors_only capture policy.

Invariants:
- Non-blocking: persistence failures are logged but never propagate into
  the request lifecycle.
- Max body size: 50MB per file; truncate with metadata if exceeded.
- Storage format matches destination architecture (§10.2): per-request
  directory with boundary-labeled JSON files.
- Redactor interface allows future PII/API key filtering without changing
  event emission or consumer structure.

Event subscriptions (errors_only policy):
- execution.chat.upstream_error — non-streaming upstream failures
- request.lifecycle.failed — request-level failures
- execution.streaming.handler_error — streaming handler errors
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


class BodyCaptureConsumer:
    """Body Capture Consumer — errors_only policy implementation.

    Captures request payloads and upstream error bodies when error events
    are emitted. Persists to ``__body_captures__/{date}/{req_id}/`` with
    boundary-labeled JSON files.

    Parameters
    ----------
    base_dir : str | Path
        Root directory for body captures. Default: project root /__body_captures__/
    policy : str
        Capture policy. Currently only ``errors_only`` is implemented.
        Use ``disabled`` to turn off all captures.
    redactor : Redactor
        Optional redaction interface applied before persistence.
        Default is NoOpRedactor (no redaction).
    """

    # Events subscribed under errors_only policy
    ERRORS_ONLY_EVENTS = {
        "execution.chat.upstream_error",
        "request.lifecycle.failed",
        "execution.streaming.handler_error",
    }

    def __init__(
        self,
        base_dir: Optional[str | Path] = None,
        policy: str = "errors_only",
        redactor: Optional[Redactor] = None,
    ) -> None:
        """Initialize BodyCaptureConsumer.

        Parameters
        ----------
        base_dir : str | Path | None
            Root directory for body captures. If None, uses project root
            /__body_captures__/ (relative to keeprollming package).
        policy : str
            Capture policy: ``errors_only`` or ``disabled``. Default: ``errors_only``.
        redactor : Redactor | None
            Redaction interface. Default: NoOpRedactor.
        """
        self._base_dir = Path(base_dir) if base_dir is not None else self._default_base_dir()
        self._policy = policy
        self._redactor = redactor or NoOpRedactor()
        self._ensure_base_dir()

    def _default_base_dir(self) -> Path:
        """Compute default capture directory at project root."""
        # Go up from keeprollming/observability/ to project root
        pkg_root = Path(__file__).resolve().parent.parent.parent
        return pkg_root / "__body_captures__"

    def _ensure_base_dir(self) -> None:
        """Create base directory if it doesn't exist."""
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # If we can't create the directory, captures will fail silently
            pass

    def __call__(self, event: RuntimeEvent) -> None:
        """Process an error event and capture bodies if policy matches.

        Parameters
        ----------
        event : RuntimeEvent
            The error event to process.
        """
        # Disabled policy — no captures
        if self._policy == "disabled":
            return

        # errors_only policy — capture on known error events
        if self._policy == "errors_only" and event.type in self.ERRORS_ONLY_EVENTS:
            self._capture_error(event)

    def _capture_error(self, event: RuntimeEvent) -> None:
        """Capture request/response bodies for an error event.

        Persists to ``__body_captures__/{date}/{req_id}/`` with boundary-labeled
        JSON files. All I/O is wrapped in try/except to ensure non-blocking.

        Parameters
        ----------
        event : RuntimeEvent
            The error event containing capture data.
        """
        try:
            req_id = event.req_id
            if not req_id:
                # No req_id — cannot create capture directory; skip silently
                return

            data = event.data
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            capture_dir = self._base_dir / date_str / self._sanitize_req_id(req_id)

            try:
                capture_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                return

            # 1. meta.json — capture metadata
            meta = {
                "req_id": req_id,
                "policy": self._policy,
                "trigger_event": event.type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "unix_ts": time.time(),
            }

            # Add route info if available
            if "route" in data:
                meta["route"] = data["route"]
            if "upstream_model" in data:
                meta["upstream_model"] = data["upstream_model"]
            if "status" in data:
                meta["status"] = data["status"]

            self._write_json(capture_dir / "meta.json", meta)

            # 2. upstream.error.json — upstream error body (if available)
            error_body = data.get("body")
            if error_body is not None and error_body != "":
                # If body is a JSON string, parse it to avoid double-encoding
                if isinstance(error_body, str):
                    try:
                        error_body = json.loads(error_body)
                    except (json.JSONDecodeError, ValueError):
                        pass  # Keep as raw string if not valid JSON
                redacted_body = self._redactor.redact(error_body)
                self._write_json(capture_dir / "upstream.error.json", redacted_body)

            # 3. request.raw_in.json — request payload (if available in event context)
            # Note: current error events don't carry full request payload;
            # this is captured when the field is present in event data.
            request_payload = data.get("request_payload") or data.get("body_json")
            if request_payload is not None:
                redacted_payload = self._redactor.redact(request_payload)
                self._write_json(capture_dir / "request.raw_in.json", redacted_payload)

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
