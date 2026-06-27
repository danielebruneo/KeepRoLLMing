"""
Payload dump utility for debugging upstream errors.

When an upstream request fails with a 4xx/5xx error, the full request payload
is dumped to a JSON file in the __dumps/ directory for later analysis.

Usage:
    from .dump import dump_failed_payload
    await dump_failed_payload(req_id, effective_payload, resp_status, resp_body, ...)
"""

import json
import os
import time
from typing import Any, Dict, Optional

# Dumps directory lives at project root, alongside __summary_cache/
_DUMPS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "__dumps__")
_MAX_DUMP_FILE_SIZE_MB = 50  # Don't write individual dumps larger than this


def _ensure_dumps_dir() -> str:
    """Create the dumps directory if it doesn't exist."""
    os.makedirs(_DUMPS_DIR, exist_ok=True)
    return _DUMPS_DIR


def _sanitize_filename(s: str) -> str:
    """Remove characters that are unsafe for filenames."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


async def dump_failed_payload(
    req_id: str,
    effective_payload: Dict[str, Any],
    resp_status: int,
    resp_body: str,
    *,
    upstream_model: Optional[str] = None,
    upstream_url: Optional[str] = None,
    route: Optional[str] = None,
) -> Optional[str]:
    """Dump the full upstream request payload to disk on upstream error.

    Args:
        req_id: Request ID from KRM
        effective_payload: The JSON payload that was sent to upstream
        resp_status: HTTP status code returned by upstream
        resp_body: Error body (truncated or full) from upstream
        upstream_model: Model name that was used
        upstream_url: URL that was called
        route: Route name that matched

    Returns:
        Path to the dump file, or None if dump was skipped.
    """
    try:
        _ensure_dumps_dir()

        ts = time.time()
        ts_str = time.strftime("%Y%m%d_%H%M%S", time.gmtime(ts))

        filename = f"{ts_str}_{sanitize(req_id)}_{resp_status}.json"
        # Trim long req_ids
        if len(filename) > 120:
            filename = filename[:60] + "..." + filename[-55:]
        filepath = os.path.join(_DUMPS_DIR, filename)

        dump = {
            "_meta": {
                "req_id": req_id,
                "timestamp": ts_str,
                "unix_ts": ts,
                "resp_status": resp_status,
                "upstream_model": upstream_model,
                "upstream_url": upstream_url,
                "route": route,
            },
            "upstream_error_body": resp_body,
            "request_payload": effective_payload,
        }

        payload_bytes = json.dumps(dump, indent=2, default=str).encode("utf-8")
        if len(payload_bytes) > _MAX_DUMP_FILE_SIZE_MB * 1024 * 1024:
            # Too large — write a summary instead
            summary = dict(dump)
            summary["request_payload"] = {
                "_truncated": True,
                "_message_count": len(effective_payload.get("messages", [])),
                "_keys": list(effective_payload.keys()),
                "_payload_size_bytes": len(payload_bytes),
            }
            summary["_note"] = (
                f"Payload exceeded {_MAX_DUMP_FILE_SIZE_MB}MB limit. "
                f"Full payload size: {len(payload_bytes)} bytes"
            )
            payload_bytes = json.dumps(summary, indent=2, default=str).encode("utf-8")

        with open(filepath, "wb") as f:
            f.write(payload_bytes)

        return filepath
    except Exception as e:
        # Never let a dump failure propagate
        from keeprollming.logger import log
        log("WARN", "dump_failed_payload_error", req_id=req_id, error=str(e))
        return None


def sanitize(s: str) -> str:
    """Sanitize a string for use in filenames."""
    return _sanitize_filename(s)
