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

from typing import Any


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
