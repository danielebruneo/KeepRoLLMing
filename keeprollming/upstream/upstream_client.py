"""HTTP client management and upstream API communication.

This module provides:
- Singleton async HTTP client with configurable timeouts
- Request/response logging hooks for observability
- Context length introspection for model configuration
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from ..config import DEFAULT_CTX_LEN, UPSTREAM_BASE_URL
from ..logger import log, log_request, log_response
from ..observability import events_upstream as _up
from ..types import DEFAULT_REQUEST_TIMEOUT

# Global client singleton
_http_client: httpx.AsyncClient | None = None

# FIX-D072: EventDispatcher for upstream emit_* calls.
# Set by app.py lifespan() after initialization.
_upstream_dispatcher: Any | None = None


def set_upstream_dispatcher(dispatcher: Any) -> None:
    """Set the EventDispatcher for upstream event emission.

    Called from app.py lifespan() after dispatcher initialization so that
    emit_* calls flow through the Projector architecture instead of bypassing
    to legacy log().
    """
    global _upstream_dispatcher
    _upstream_dispatcher = dispatcher


# Context length cache: {model_name: (ctx_len, last_updated_timestamp)}
_ctx_cache: Dict[str, Tuple[int, float]] = {}
_CTX_TTL_SEC = 60.0


class UpstreamClient:
    """Async HTTP client wrapper for upstream API communication.
    
    This class encapsulates the httpx.AsyncClient with built-in hooks for
    request/response logging and provides a clean interface for making
    upstream API calls.
    
    Attributes:
        client: The underlying httpx.AsyncClient instance
        timeout: Configured timeout in seconds
    """
    
    def __init__(self, request_timeout: float = DEFAULT_REQUEST_TIMEOUT):
        """Initialize upstream HTTP client.
        
        Args:
            request_timeout: Timeout for requests in seconds
        """
        self._timeout = request_timeout
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _on_request(self, request: httpx.Request) -> None:
        """Request event hook - logs incoming requests."""
        request.extensions["ts_start"] = time.perf_counter()
        await log_request(request)
    
    async def _on_response(self, response: httpx.Response) -> None:
        """Response event hook - logs responses and handles SSE special cases."""
        ts_start = response.request.extensions.get("ts_start")
        elapsed_ms = (time.perf_counter() - ts_start) * 1000.0 if ts_start else None
        
        ct = (response.headers.get("content-type") or "").lower()
        
        # Handle SSE streaming responses
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

        # Non-streaming: body is available
        await log_response(response, elapsed_ms=elapsed_ms)
    
    async def get_client(self) -> httpx.AsyncClient:
        """Get or create the async HTTP client instance.
        
        Returns:
            Configured httpx.AsyncClient instance
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=60.0),
                event_hooks={"request": [self._on_request], "response": [self._on_response]},
            )
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


async def http_client(request_timeout: float = DEFAULT_REQUEST_TIMEOUT) -> httpx.AsyncClient:
    """Get the global HTTP client instance (singleton pattern).
    
    This is a convenience function that returns the global client.
    For more control, use UpstreamClient class directly.
    
    Args:
        request_timeout: Timeout for requests in seconds
        
    Returns:
        Configured httpx.AsyncClient instance
    """
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
            timeout=httpx.Timeout(request_timeout, connect=60.0),
            event_hooks={"request": [_on_request], "response": [_on_response]},
        )
    
    return _http_client


async def close_http_client() -> None:
    """Close the global HTTP client instance."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _extract_ctx_len_from_model_obj(obj: Optional[Dict[str, Any]]) -> Optional[Tuple[int, str]]:
    """Extract context length from model metadata dictionary.
    
    Args:
        obj: Model metadata dictionary to parse
        
    Returns:
        Tuple of (ctx_len, source_field) or None if not found
    """
    if not isinstance(obj, dict):
        return None
    
    # Try various field names in order of preference
    for k in (
        "loaded_context_length",
        "context_length", 
        "context_window",
        "n_ctx",
        "ctx_len",
        "max_context_length"
    ):
        v = obj.get(k)
        if isinstance(v, int) and v > 0:
            return (v, k)
    
    return None


async def get_ctx_len_for_model(upstream_model: str) -> int:
    """Get context length for a model by introspecting upstream API.
    
    Attempts multiple endpoint paths to fetch model metadata, with fallback
    to DEFAULT_CTX_LEN if all attempts fail.
    
    Args:
        upstream_model: Model name to look up
        
    Returns:
        Context length in tokens
    """
    now = time.time()
    cached = _ctx_cache.get(upstream_model)
    
    # Check cache first
    if cached and (now - cached[1]) < _CTX_TTL_SEC:
        return cached[0]
    
    # Try multiple endpoint paths
    url_list = [
        f"{UPSTREAM_BASE_URL}/api/v0/models",
        f"{UPSTREAM_BASE_URL}/v1/models", 
        f"{UPSTREAM_BASE_URL}/v0/models"
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
            
            # Find the specific model
            chosen = None
            for m in models:
                if isinstance(m, dict) and m.get("id") == upstream_model:
                    chosen = m
                    break
            
            # Extract context length
            ctx_tuple = _extract_ctx_len_from_model_obj(chosen)
            
            if ctx_tuple is None:
                # Fallback: try first model with any ctx field
                for m in models:
                    ctx_tuple = _extract_ctx_len_from_model_obj(m if isinstance(m, dict) else None)
                    if ctx_tuple:
                        break
            
            if ctx_tuple is None:
                ctx_len = DEFAULT_CTX_LEN
                ctx_src = "default"
            else:
                ctx_len, ctx_src = ctx_tuple
            
            # Cache the result
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

    # All endpoints failed
    _up.emit_all_endpoints_failed(
        upstream_model=upstream_model,
        ctx_len=DEFAULT_CTX_LEN,
        dispatcher=_upstream_dispatcher,
    )
    return DEFAULT_CTX_LEN
