"""Upstream module — HTTP client management and upstream API communication.

Moved from keeprollming/upstream.py as part of modularization.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import httpx

from ..config import DEFAULT_CTX_LEN, UPSTREAM_BASE_URL
from ..logger import log_request, log_response
from ..observability import events_upstream as _up
from ..types import DEFAULT_REQUEST_TIMEOUT

_http_client: httpx.AsyncClient | None = None
_upstream_dispatcher: Any | None = None
_transport_options: dict[str, float | int | None] = {
    "max_connections": 100,
    "max_keepalive_connections": 20,
    "keepalive_expiry": 30.0,
    "pool_timeout": 10.0,
    "connect_timeout": 60.0,
}

_ctx_cache: Dict[str, Tuple[int, float]] = {}
_CTX_TTL_SEC = 60.0


def set_upstream_dispatcher(dispatcher: Any | None) -> None:
    """Set the dispatcher used by the canonical shared upstream client.

    The process-wide client lives in this module.  Keeping its event hooks and
    dispatcher in the same owner prevents a compatibility module from silently
    maintaining a second, unused transport state.
    """
    global _upstream_dispatcher
    _upstream_dispatcher = dispatcher


def get_http_transport_settings() -> dict[str, float | int]:
    """Return a copy of the effective shared-pool policy for diagnostics."""
    return {
        "max_connections": int(_transport_options["max_connections"]),
        "max_keepalive_connections": int(_transport_options["max_keepalive_connections"]),
        "keepalive_expiry": float(_transport_options["keepalive_expiry"]),
        "pool_timeout": float(_transport_options["pool_timeout"]),
        "connect_timeout": float(_transport_options["connect_timeout"]),
    }


def configure_http_transport(config: dict[str, Any] | None = None) -> None:
    """Configure the shared HTTP pool before it is created.

    Request deadlines are intentionally *not* client settings: routes can have
    different deadlines while all requests must share one bounded pool.
    """
    global _transport_options
    values = config or {}
    _transport_options = {
        "max_connections": max(1, int(values.get("max_connections", 100))),
        "max_keepalive_connections": max(0, int(values.get("max_keepalive_connections", 20))),
        "keepalive_expiry": max(0.0, float(values.get("keepalive_expiry", 30.0))),
        "pool_timeout": max(0.01, float(values.get("pool_timeout", 10.0))),
        "connect_timeout": max(0.01, float(values.get("connect_timeout", 60.0))),
    }


def make_request_timeout(timeout: float) -> httpx.Timeout:
    """Build a per-request timeout without changing the shared pool policy."""
    return httpx.Timeout(
        float(timeout),
        connect=float(_transport_options["connect_timeout"]),
        pool=float(_transport_options["pool_timeout"]),
    )


async def http_client(request_timeout: float = DEFAULT_REQUEST_TIMEOUT) -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:

        async def _on_request(request: httpx.Request) -> None:
            request.extensions["ts_start"] = time.perf_counter()
            await log_request(request)

        async def _on_response(response: httpx.Response) -> None:
            ts_start = response.request.extensions.get("ts_start")
            elapsed_ms = (time.perf_counter() - ts_start) * 1000.0 if ts_start else None

            ct = (response.headers.get("content-type") or "").lower()

            if ct.startswith("text/event-stream"):
                _up.emit_response_received(
                    url=str(response.request.url),
                    method=response.request.method,
                    status=response.status_code,
                    elapsed_ms=elapsed_ms,
                    headers=dict(response.headers),
                    body="",
                    note="SSE response headers received (body logged by stream tee when consumed)",
                    dispatcher=_upstream_dispatcher,
                )
                return

            await log_response(response, elapsed_ms=elapsed_ms)

        _http_client = httpx.AsyncClient(
            # Call sites pass their route-specific timeout explicitly. The
            # default remains defensive for less specialised callers.
            timeout=make_request_timeout(request_timeout),
            limits=httpx.Limits(
                max_connections=int(_transport_options["max_connections"]),
                max_keepalive_connections=int(_transport_options["max_keepalive_connections"]),
                keepalive_expiry=float(_transport_options["keepalive_expiry"]),
            ),
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )

    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _extract_ctx_len_from_model_obj(obj: Optional[Dict[str, Any]]) -> Optional[Tuple[int, str]]:
    if not isinstance(obj, dict):
        return None
    for k in (
        "loaded_context_length",
        "context_length",
        "context_window",
        "n_ctx",
        "ctx_len",
        "max_context_length",
    ):
        v = obj.get(k)
        if isinstance(v, int) and v > 0:
            return (v, k)
    return None


async def get_ctx_len_for_model(upstream_model: str) -> int:
    now = time.time()
    cached = _ctx_cache.get(upstream_model)
    if cached and (now - cached[1]) < _CTX_TTL_SEC:
        return cached[0]

    url_list = [
        f"{UPSTREAM_BASE_URL}/api/v0/models",
        f"{UPSTREAM_BASE_URL}/v1/models",
        f"{UPSTREAM_BASE_URL}/v0/models",
    ]
    for url in url_list:
        try:
            client = await http_client(request_timeout=30.0)
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            models = data.get("data") if isinstance(data, dict) else None
            if not isinstance(models, list):
                raise ValueError(f"Unexpected {url} format")

            chosen = None
            for m in models:
                if isinstance(m, dict) and m.get("id") == upstream_model:
                    chosen = m
                    break

            ctx_tuple = _extract_ctx_len_from_model_obj(chosen)
            if ctx_tuple is None:
                for m in models:
                    ctx_tuple = _extract_ctx_len_from_model_obj(m if isinstance(m, dict) else None)
                    if ctx_tuple:
                        break

            if ctx_tuple is None:
                ctx_len = DEFAULT_CTX_LEN
                ctx_src = "default"
            else:
                ctx_len, ctx_src = ctx_tuple

            _ctx_cache[upstream_model] = (ctx_len, now)
            _up.emit_ctx_len(
                upstream_model=upstream_model,
                ctx_len=ctx_len,
                source=f"{url}:{ctx_src}" if ctx_len != DEFAULT_CTX_LEN else "default",
                dispatcher=_upstream_dispatcher,
            )
            return ctx_len
        except Exception as e:
            _up.emit_ctx_len_fallback(
                upstream_model=upstream_model,
                ctx_len=DEFAULT_CTX_LEN,
                err=str(e),
                dispatcher=_upstream_dispatcher,
            )
            _ctx_cache[upstream_model] = (DEFAULT_CTX_LEN, now)
            continue

    _up.emit_all_endpoints_failed(
        upstream_model=upstream_model,
        ctx_len=DEFAULT_CTX_LEN,
        dispatcher=_upstream_dispatcher,
    )
    return DEFAULT_CTX_LEN


__all__ = [
    "configure_http_transport",
    "get_http_transport_settings",
    "set_upstream_dispatcher",
    "make_request_timeout",
    "http_client",
    "close_http_client",
    "get_ctx_len_for_model",
    "resolve_fallback_chain",
    "prepare_upstream_request",
]

# Re-export from fallback_chain for backward compatibility
from .fallback_chain import resolve_fallback_chain  # noqa: E402, F401


def prepare_upstream_request(
    payload: dict,
    upstream_model: str,
    messages: list,
    route=None,
    max_tokens_req=None,
    ctx_eff: int = 8192,
    req_id: str = "",
) -> dict:
    """Build the full upstream request payload with all inference parameters.

    This function replaces the inline _build_upstream_payload in chat_completions.py.
    Filters that need to make retry calls (e.g., ModelNudgeFilter) should use this
    to ensure all route-level inference parameters (temperature, max_tokens, top_p,
    overrides) are included in the retry request.
    """
    upstream_payload = dict(payload)
    upstream_payload["_original_model"] = upstream_payload.get("model", payload.get("model", ""))
    upstream_payload["model"] = upstream_model
    upstream_payload["messages"] = list(messages)

    # Apply route-level overrides
    if route and getattr(route, "overrides", None):
        from ..overrides import apply_overrides

        applied = apply_overrides(upstream_payload, route.overrides)
        if applied:
            for key, old_val, new_val in applied:
                _up.emit_override_applied(
                    req_id=req_id, param=key, old_value=old_val, new_value=new_val
                )

    return upstream_payload
