"""Embeddings endpoint handler.

This module provides the /v1/embeddings endpoint with upstream proxying support.
"""

import os
from typing import Any, Dict

import httpx
from fastapi.responses import JSONResponse

from ..logger import log
from ..observability import events_embeddings as _emb
from ..config import UPSTREAM_BASE_URL, resolve_route, get_route_settings
from ..auth import bearer_token, authentication_error_response, is_authorized
from ..observability import events_request as _request_events


async def embeddings_handler(
    payload: Dict[str, Any],
    headers: Dict[str, str],
    req_id: str,
) -> JSONResponse:
    """Handle /v1/embeddings endpoint requests.

    Args:
        payload: Request payload dict
        headers: Request headers dict
        req_id: Request ID string

    Returns:
        JSONResponse from upstream or error response
    """
    client_model = payload.get("model", "")

    # Resolve route to get per-route upstream URL and settings
    route, model = resolve_route(client_model)

    if route:
        rs = get_route_settings(route, model)
        base_url = (rs.upstream_url or UPSTREAM_BASE_URL).rstrip("/")
        request_timeout = rs.request_timeout
    else:
        base_url = UPSTREAM_BASE_URL.rstrip("/")
        request_timeout = 120.0

    if route is None or not is_authorized(headers, route.api_keys or []):
        from ..app import get_event_dispatcher
        _request_events.emit_auth_rejected(
            req_id,
            route=route.name if route is not None else "unresolved",
            endpoint="/v1/embeddings",
            credential_present=bearer_token(headers) is not None,
            dispatcher=get_event_dispatcher(),
        )
        return authentication_error_response()

    _emb.emit_request(
        req_id=req_id,
        model=payload.get("model"),
        input_length=len(payload.get("input", [])),
        upstream_url=base_url,
        route=route.name if route else "default",
    )

    # Emit debug event unconditionally; projector configuration decides visibility.
    # Projector level filtering controls visibility (structured@TRACE captures it,
    # main@BASIC filters it out).
    from ..logger import snip_json
    _emb.emit_request_debug(req_id=req_id, body_json=snip_json(payload))

    try:
        url = f"{base_url}/v1/embeddings"
        async with httpx.AsyncClient(timeout=httpx.Timeout(request_timeout)) as client:
            upstream_headers = dict(rs.upstream_headers)
            if rs.api_key and "Authorization" not in upstream_headers:
                upstream_headers["Authorization"] = f"Bearer {rs.api_key}"
            r = await client.post(url, json=payload, headers=upstream_headers)
            r.raise_for_status()
            return JSONResponse(r.json(), status_code=r.status_code)

    except httpx.ConnectError as e:
        _emb.emit_failed(req_id=req_id, error=str(e), upstream_url=base_url)
        return JSONResponse(
            {"error": {"message": f"Failed to connect to upstream {base_url}: {str(e)}"}},
            status_code=502
        )
    except httpx.TimeoutException:
        _emb.emit_timeout(req_id=req_id, upstream_url=base_url)
        return JSONResponse(
            {"error": {"message": f"Request timeout connecting to {base_url}"}},
            status_code=504
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _emb.emit_failed(req_id=req_id, error=str(e), traceback=tb, upstream_url=base_url)
        return JSONResponse(
            {"error": {"message": f"Failed to process embeddings request: {str(e)}"}},
            status_code=500
        )


async def process_embedding_request(
    payload: Dict[str, Any],
    headers: Dict[str, str]
) -> JSONResponse:
    """Process an embeddings request.

    Args:
        payload: Request payload dict
        headers: Request headers dict

    Returns:
        JSONResponse from upstream
    """
    req_id = os.urandom(6).hex()

    try:
        url = f"{UPSTREAM_BASE_URL}/v1/embeddings"
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return JSONResponse(r.json(), status_code=r.status_code)

    except httpx.ConnectError as e:
        _emb.emit_failed(req_id=req_id, error=str(e), upstream_url=UPSTREAM_BASE_URL)
        return JSONResponse(
            {"error": {"message": f"Failed to connect to upstream {UPSTREAM_BASE_URL}: {str(e)}"}},
            status_code=502
        )
    except httpx.TimeoutException:
        _emb.emit_timeout(req_id=req_id)
        return JSONResponse(
            {"error": {"message": "Request timeout"}},
            status_code=504
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _emb.emit_failed(req_id=req_id, error=str(e), traceback=tb)
        return JSONResponse(
            {"error": {"message": f"Failed to process embeddings request: {str(e)}"}},
            status_code=500
        )
