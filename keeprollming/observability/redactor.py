"""Redaction interface for BodyCaptureConsumer.

Defines the Redactor abstraction that BodyCaptureConsumer uses before
persisting captured data. Initial implementation is no-op; future phases
can plug in configurable PII/API key redaction policies.

Invariants:
- Redactor is a consumer-side concern (INV-04): events carry raw data,
  redaction happens at persistence time.
- NoOpRedactor is the default — captures everything unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .events import RuntimeEvent


class Redactor:
    """Interface for redacting sensitive data before body capture persistence."""

    def redact(self, data: Any) -> Any:
        """Redact sensitive fields from arbitrary data.

        Parameters
        ----------
        data : Any
            The data to redact. May be a dict, list, str, or primitive.

        Returns
        -------
        Any
            Redacted version of the input data.
        """
        raise NotImplementedError


class NoOpRedactor(Redactor):
    """No-op redactor — passes data through unchanged.

    Default implementation for BodyCaptureConsumer. Enables full-fidelity
    capture until a redaction policy is configured.
    """

    def redact(self, data: Any) -> Any:
        return data


class ZeroContentRedactor(Redactor):
    """Preserve operational facts while removing every unclassified string.

    This is intentionally stricter than a PII detector: operators selecting
    privacy mode ask KRM not to persist transcript-like content at all. String
    values are therefore omitted unless their field is a known operational
    identifier (route, model, status reason, etc.).
    """

    _SAFE_STRING_FIELDS = {
        "route",
        "route_name",
        "resolved_route",
        "upstream_model",
        "model",
        "upstream_url",
        "url",
        "endpoint",
        "method",
        "phase",
        "boundary",
        "direction",
        "finish_reason",
        "error_type",
        "component",
        "policy",
        "state",
        "client_model",
        "type",
        "code",
    }

    def redact(self, data: Any) -> Any:
        return self._redact(data)

    def redact_event(self, event: RuntimeEvent) -> RuntimeEvent:
        """Return a safe projection without mutating the authoritative event."""
        return replace(event, data=self._redact(event.data))

    def _redact(self, value: Any, *, field: str | None = None) -> Any:
        if isinstance(value, bytes):
            return {"_content_omitted": True, "byte_length": len(value)}
        if isinstance(value, str):
            return value if field in self._SAFE_STRING_FIELDS else "[content omitted]"
        if isinstance(value, dict):
            return {str(key): self._redact(item, field=str(key)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item, field=field) for item in value]
        return value
