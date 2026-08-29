"""Client API-key authentication for route-aware API endpoints.

``Route.api_key`` authenticates KRM to an upstream provider.  This module
handles the separate client-to-KRM credential declared as ``api_keys``.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence

from fastapi.responses import JSONResponse

_REDACTED_HEADERS = frozenset({"authorization", "x-api-key", "proxy-authorization"})


def redact_sensitive_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return headers safe for diagnostic events and traces."""
    return {
        key: "[REDACTED]" if key.lower() in _REDACTED_HEADERS else value
        for key, value in headers.items()
    }


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.lower()
    for key, value in headers.items():
        if key.lower() == expected:
            return value
    return None


def bearer_token(headers: Mapping[str, str]) -> str | None:
    """Extract an OpenAI-compatible ``Authorization: Bearer`` credential."""
    authorization = _header(headers, "authorization")
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def is_authorized(headers: Mapping[str, str], accepted_keys: Sequence[str]) -> bool:
    """Check a presented bearer token against route keys in constant time."""
    if not accepted_keys:
        return True
    token = bearer_token(headers)
    return token is not None and any(
        hmac.compare_digest(token, accepted_key) for accepted_key in accepted_keys
    )


def authentication_error_response() -> JSONResponse:
    """Return the OpenAI-shaped response used for absent or invalid keys."""
    return JSONResponse(
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
        content={
            "error": {
                "message": "Invalid API key",
                "type": "authentication_error",
                "code": "invalid_api_key",
            }
        },
    )
