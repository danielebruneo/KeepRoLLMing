"""Client API-key authentication for route-aware API endpoints.

``Route.api_key`` authenticates KRM to an upstream provider.  This module
handles the separate client-to-KRM credential declared as ``api_keys``.
"""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Mapping, Sequence

from fastapi.responses import JSONResponse

_REDACTED_HEADERS = frozenset({"authorization", "x-api-key", "proxy-authorization"})


def redact_sensitive_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return headers safe for diagnostic events and traces."""
    return {key: "[REDACTED]" if key.lower() in _REDACTED_HEADERS else value for key, value in headers.items()}


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
    return token is not None and any(hmac.compare_digest(token, accepted_key) for accepted_key in accepted_keys)


def resolve_client_ip(
    peer_ip: str | None,
    headers: Mapping[str, str],
    trusted_proxies: Sequence[str],
) -> str | None:
    """Resolve the client IP, honoring X-Forwarded-For only behind trusted peers.

    The XFF chain is walked from the socket peer toward the original client.
    The first address not in the trusted-proxy set is the effective client.
    Malformed chains and trusted peers that omit XFF resolve to unknown.
    """
    try:
        peer = ipaddress.ip_address(peer_ip) if peer_ip else None
        proxies = tuple(ipaddress.ip_network(item, strict=False) for item in trusted_proxies)
    except ValueError:
        return None
    if peer is None:
        return None

    def trusted(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(address.version == network.version and address in network for network in proxies)

    if not trusted(peer):
        return str(peer)

    forwarded = _header(headers, "x-forwarded-for")
    if not forwarded:
        return None
    try:
        chain = [ipaddress.ip_address(item.strip()) for item in forwarded.split(",")]
    except ValueError:
        return None
    if not chain:
        return None

    current = peer
    for candidate in reversed(chain):
        if not trusted(current):
            break
        current = candidate
    return str(current) if not trusted(current) else None


def is_client_ip_allowed(
    peer_ip: str | None,
    headers: Mapping[str, str],
    *,
    mode: str,
    lan_networks: Sequence[str],
    trusted_proxies: Sequence[str],
) -> bool:
    """Evaluate the network portion of route access; API keys are separate."""
    if mode in {"any", "public"}:
        return True
    if mode != "lan":
        return False
    client_ip = resolve_client_ip(peer_ip, headers, trusted_proxies)
    if client_ip is None:
        return False
    try:
        address = ipaddress.ip_address(client_ip)
        return any(
            address.version == network.version and address in network
            for network in (ipaddress.ip_network(item, strict=False) for item in lan_networks)
        )
    except ValueError:
        return False


def is_request_client_allowed(
    peer_ip: str | None,
    headers: Mapping[str, str],
    *,
    mode: str | None = None,
) -> bool:
    """Apply the current global policy or one route's resolved access mode."""
    from .config import CONFIG, DEFAULTS

    policy = CONFIG.get("client_access", {})
    effective_mode = mode or DEFAULTS.client_access
    return is_client_ip_allowed(
        peer_ip,
        headers,
        mode=effective_mode,
        lan_networks=policy.get("lan_networks", []),
        trusted_proxies=policy.get("trusted_proxies", []),
    )


def client_access_error_response() -> JSONResponse:
    """Return a generic response when the caller is outside the route policy."""
    return JSONResponse(
        status_code=403,
        content={
            "error": {
                "message": "Client network is not allowed",
                "type": "access_denied",
                "code": "client_network_not_allowed",
            }
        },
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
