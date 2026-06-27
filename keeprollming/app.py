"""Minimal orchestration layer for KeepRollMing API.

This is the main entry point that delegates all business logic to modular components.
The original monolithic app.py (2,295 lines) has been extracted into:
- keeprollming/endpoints/ - API endpoint handlers
- keeprollming/streaming/ - SSE handling and transformations
- keeprollming/processing/ - Summarization and message processing
- keeprollming/routing/ - Route resolution and HTTP client

Original file preserved as: keeprollming/app.py.orig
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from .config import (
    CONFIG,
    DEFAULTS,
    resolve_route,
    resolve_fallback_chain,
    get_route_settings,
)
from .logger import log


def _parse_captured_sse_text(sse_text: str) -> tuple[str, str | None, dict | None, int]:
    """Parse raw SSE text to extract assistant message and metadata.

    This function is used when direct chunk parsing fails (e.g., for final logging)
    to reconstruct the response from captured SSE data.

    Args:
        sse_text: Raw SSE text content

    Returns:
        Tuple of (assistant_text, finish_reason, usage_dict, event_count)
    """
    assistant_parts: list[str] = []
    finish_reason: str | None = None
    final_usage: dict | None = None
    event_count = 0
    buf = sse_text

    while True:
        m_sep = re.search(r"\r?\n\r?\n", buf)
        if not m_sep:
            break

        block = buf[: m_sep.start()]
        buf = buf[m_sep.end() :]

        data_lines: list[str] = []
        for line in block.splitlines():
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if not data_lines:
            continue

        payload_sse = "\n".join(data_lines).strip()
        if not payload_sse or payload_sse == "[DONE]":
            continue

        event_count += 1
        try:
            obj = json.loads(payload_sse)
        except Exception:
            continue

        if not isinstance(obj, dict):
            continue

        if isinstance(obj.get("usage"), dict):
            final_usage = obj.get("usage")

        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            c0 = choices[0] if isinstance(choices[0], dict) else None
            if isinstance(c0, dict):
                for candidate in (c0.get("delta"), c0.get("message")):
                    if isinstance(candidate, dict):
                        piece = candidate.get("content")
                        if isinstance(piece, str) and piece:
                            assistant_parts.append(piece)

                fr = c0.get("finish_reason")
                if isinstance(fr, str) and fr:
                    finish_reason = fr

    return "".join(assistant_parts).strip(), finish_reason, final_usage, event_count


# ----------------------------
# Application Lifecycle
# ----------------------------
# ----------------------------
# Application Lifecycle
# ----------------------------

async def _config_watcher():
    """Background task to watch for config file changes."""
    from .logger import log as log_app, log_config_reload as log_reload_event
    from .config import check_config_reload, get_config_mtime

    while True:
        try:
            result = check_config_reload()
            if result:
                current = get_config_mtime() or 0.0
                log_reload_event(current - 1.0, current)
        except Exception as e:
            log_app("ERROR", "config_reload_error", message=f"Config reload error: {e}")

        # Check every 2 seconds
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup/shutdown hooks."""
    from .logger import log, setup_server_logging
    from .performance import set_performance_logs_dir, set_summary_interval
    from .async_log_writer import get_async_writer, start_async_writer, stop_async_writer

    # Setup file-based server logging
    logger = setup_server_logging()

    # Start the async log writer (replaces sync flush() in hot path)
    await start_async_writer()
    # Register debug sink for streaming handler
    get_async_writer().register_sink("stream_debug", "/tmp/streaming_final_chunk.log")

    # Configure performance logs directory from config
    perf_logs_dir = CONFIG.get("defaults", {}).get("performance_logs_dir", "__performance_logs")
    set_performance_logs_dir(perf_logs_dir)

    # Configure summary update interval (reduce I/O overhead)
    summary_interval = CONFIG.get("performance", {}).get("summary_update_interval", 100)
    set_summary_interval(summary_interval)
    log("INFO", "perf_logs_dir", message=f"Performance logs directory: {perf_logs_dir}")

    # Initial config reload check (in case config changed before startup)
    from .config import check_config_reload
    if check_config_reload():
        log("INFO", "config_reloaded", message="Config was modified before startup, reloading...")

    # Start the config watcher background task
    asyncio.create_task(_config_watcher())

    log("INFO", "app_starting", message="Starting...")
    try:
        yield
    finally:
        log("INFO", "app_stopping", message="Shutting down...")
        await stop_async_writer()


# ----------------------------
# FastAPI Application
# ----------------------------
app = FastAPI(
    title="KeepRollMing API",
    description="LLM Request Orchestrator with streaming and summarization",
    version=os.getenv("APP_VERSION", "0.1.0"),
    lifespan=lifespan,
)


# ----------------------------
# Request Middleware
# ----------------------------
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Generate unique request ID and attach to request state."""
    # Generate req_id if not present (e.g., from X-Request-ID header)
    req_id = getattr(request.state, 'req_id', None)
    if not req_id:
        # Check for header first
        req_id = request.headers.get("x-request-id")
    if not req_id:
        # Generate unique ID (8 chars)
        req_id = uuid.uuid4().hex[:8]
    
    # Attach to request state for downstream access
    request.state.req_id = req_id
    
    response = await call_next(request)
    return response


# ----------------------------
# Exception Handlers
# ----------------------------
@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Custom 404 handler with logging."""
    log("WARNING", "not_found", path=request.url.path)
    return JSONResponse(
        status_code=404,
        content={"detail": f"Path '{request.url.path}' not found"},
    )


# ----------------------------
# System Endpoints
# ----------------------------
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/metrics")
async def get_metrics():
    """Metrics endpoint - delegates to metrics module."""
    from .metrics import METRICS_COLLECTOR
    return METRICS_COLLECTOR.get_metrics()


@app.get("/v1/models")
async def list_models():
    """List available models (routes)."""
    from .config import USER_ROUTES, DEFAULTS
    

    models = []

    # Add configured routes (public only) as "models"
    for route in sorted(USER_ROUTES, key=lambda r: r.name):
        # Skip private routes and template/base routes
        if route._is_private or route.name.startswith("base/"):
            continue

        # Build config dict with resolved route details
        filters_list = None
        if isinstance(route.filter_chain, dict) and route.filter_chain.get("order"):
            filters_list = list(route.filter_chain["order"])
        route_config = {
            "upstream_model": route.model if route.model is not None else None,
            "max_tokens": route.max_tokens if route.max_tokens is not None else None,
            "summary_enabled": route.summary_enabled if route.summary_enabled is not None else False,
            "passthrough_enabled": getattr(route, 'passthrough_enabled', False),
            "pattern": route.pattern,
        }
        if filters_list:
            route_config["filters"] = filters_list

        ctx_len = route.ctx_len
        if ctx_len is None:
            ctx_len = DEFAULTS.ctx_len if hasattr(DEFAULTS, 'ctx_len') else 4096

        models.append({
            "id": route.name,
            "object": "model",
            "context_length": int(ctx_len),
            "owned_by": "orchestrator",
            "config": route_config,
        })

    return {"data": models}


# ----------------------------
# API Endpoints (Delegated to Modular Handlers)
# ----------------------------
@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    """Chat completions endpoint - delegates to endpoints.chat_completions."""

    from .endpoints.chat_completions import process_chat_request
    from fastapi.responses import JSONResponse

    # Extract payload and headers for the new handler signature
    try:
        payload = await req.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": f"Request body is not valid JSON: {str(e)}"}
        )
    
    headers = {k: v for k, v in req.headers.items()}
    # Generate req_id if not present (should be added by middleware)
    req_id = getattr(req.state, 'req_id', getattr(req, 'req_id', '-'))

    result = await process_chat_request(payload, headers, req_id)

    # Handle StreamingResponse (async iterators)
    if hasattr(result, '__aiter__'):
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={"X-Content-Type": "application/json"}
        )

    return result


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    """Embeddings endpoint - delegates to endpoints.embeddings."""
    from .endpoints.embeddings import embeddings_handler
    
    # Extract payload and headers for the new handler signature
    payload = await req.json()
    headers = {k: v for k, v in req.headers.items()}
    req_id = getattr(req.state, 'req_id', '-')
    
    result = await embeddings_handler(payload, headers, req_id)
    return result


# ----------------------------
# Legacy Exports for Test Compatibility
# ----------------------------
# These re-exports maintain compatibility with existing tests that import
# directly from keeprollming.app. They delegate to the actual implementations
# in the modular structure.

from .logger import (
    BASIC_SNIP_CHARS,
    LOG_MODE,
    LOG_MODE_CHOICES,
    LOG_SNIP_CHARS,
    classify_messages,
    extract_last_user_text,
    log_connection_error,
    log_fallback_error,
    log_streaming_response,
    summarize_request_payload,
    summarize_response_payload,
    _snip_obj_active,
    snip_json,
)

from .summary_cache import (
    conversation_fingerprint,
    find_best_prefix_entry_with_reasons,
    load_cache_entries,
    make_cache_entry,
    save_cache_entry,
)

from .metrics import (
    METRICS_COLLECTOR,
    record_conversation_metrics,
    record_summary_cache_hit,
    record_summary_cache_miss,
    record_summary_reuse,
)

from .performance import record_request_performance

from .tool_rewrite import ToolCallRewriter

from .upstream import (
    close_http_client,
    get_ctx_len_for_model,
    http_client,
)

# Token counter singleton
from .token_counter import TokenCounter
TOK = TokenCounter()

# Configuration constants
from .config import (
    SAFETY_MARGIN_TOK,
    SUMMARY_MODE,
    SUMMARY_CACHE_ENABLED,
    SUMMARY_CACHE_DIR,
    SUMMARY_CACHE_FINGERPRINT_MSGS,
    SUMMARY_FORCE_CONSOLIDATE,
    SUMMARY_CONSOLIDATE_WHEN_NEEDED,
    UPSTREAM_BASE_URL,
    DEFAULT_MAX_COMPLETION_TOKENS,
)

# Legacy summary functions for test compatibility
from .rolling_summary import (
    summarize_middle,
    summarize_incremental,
    should_summarise,
    split_messages,
    build_messages_from_summary_prefix,
    build_repacked_messages,
    choose_append_until_idx,
    ensure_repacked_has_user_message,
    _pinned_head_count,
    is_summary_cacheable,
)

# Route resolution (delegates to routing module)
def _get_fake_upstream(app_module=None):
    """Legacy function for tests - returns global fake client from conftest."""
    try:
        from tests.conftest import _fake_upstream
        return _fake_upstream
    except ImportError:
        return None
