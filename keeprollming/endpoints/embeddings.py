"""Embeddings endpoint handler.

This module provides the /v1/embeddings endpoint with upstream proxying support.
"""

import os
from typing import Any, Dict

import httpx
from fastapi.responses import JSONResponse

from ..logger import log, LOG_MODE
from ..config import UPSTREAM_BASE_URL, resolve_route, get_route_settings


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

    log(
        "INFO",
        "embedding_request",
        req_id=req_id,
        model=payload.get("model"),
        input_length=len(payload.get("input", [])),
        upstream_url=base_url,
        route=route.name if route else "default",
    )

    if LOG_MODE == "DEBUG":
        from ..logger import snip_json
        log("INFO", "embedding_request_debug", req_id=req_id, body_json=snip_json(payload))

    try:
        url = f"{base_url}/v1/embeddings"
        async with httpx.AsyncClient(timeout=httpx.Timeout(request_timeout)) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return JSONResponse(r.json(), status_code=r.status_code)

    except httpx.ConnectError as e:
        log("ERROR", "embedding_request_failed", req_id=req_id, error=str(e), upstream_url=base_url)
        return JSONResponse(
            {"error": {"message": f"Failed to connect to upstream {base_url}: {str(e)}"}},
            status_code=502
        )
    except httpx.TimeoutException:
        log("ERROR", "embedding_request_timeout", req_id=req_id, upstream_url=base_url)
        return JSONResponse(
            {"error": {"message": f"Request timeout connecting to {base_url}"}},
            status_code=504
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log("ERROR", "embedding_request_failed", req_id=req_id, error=str(e), traceback=tb, upstream_url=base_url)
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
        log("ERROR", "embedding_request_failed", req_id=req_id, error=str(e), upstream_url=UPSTREAM_BASE_URL)
        return JSONResponse(
            {"error": {"message": f"Failed to connect to upstream {UPSTREAM_BASE_URL}: {str(e)}"}},
            status_code=502
        )
    except httpx.TimeoutException:
        log("ERROR", "embedding_request_timeout", req_id=req_id)
        return JSONResponse(
            {"error": {"message": "Request timeout"}},
            status_code=504
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log("ERROR", "embedding_request_failed", req_id=req_id, error=str(e), traceback=tb)
        return JSONResponse(
            {"error": {"message": f"Failed to process embeddings request: {str(e)}"}},
            status_code=500
        )
