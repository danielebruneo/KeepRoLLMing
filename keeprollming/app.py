from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

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
    resolve_route,
    resolve_fallback_chain,
    get_route_settings,
    CONFIG,
    DEFAULTS,
    resolve_route_settings,
    USER_ROUTES,
)
from .routing import Route
from .logger import (
    BASIC_SNIP_CHARS,
    LOG_MODE,
    LOG_MODE_CHOICES,
    LOG_SNIP_CHARS,
    classify_messages,
    extract_last_user_text,
    log,
    log_connection_error,
    log_fallback_error,
    log_streaming_response,
    summarize_request_payload,
    summarize_response_payload,
    _snip_obj_active,
    snip_json,
)
from .rolling_summary import (
    build_messages_from_summary_prefix,
    build_repacked_messages,
    choose_append_until_idx,
    should_summarise,
    split_messages,
    summarize_incremental,
    summarize_middle,
    ensure_repacked_has_user_message,
    _pinned_head_count,
    is_summary_cacheable,
)
from .summary_cache import conversation_fingerprint, find_best_prefix_entry_with_reasons, load_cache_entries, make_cache_entry, save_cache_entry
from .token_counter import TokenCounter
from .upstream import close_http_client, get_ctx_len_for_model, http_client
from .performance import record_request_performance
from .metrics import (
    METRICS_COLLECTOR,
    record_conversation_metrics,
    record_summary_cache_hit,
    record_summary_cache_miss,
    record_summary_reuse
)

# ----------------------------
# Token counter
# ----------------------------
TOK = TokenCounter()

# Max chars for logging large payloads (input conversation, summary requests, etc.)
LOG_PAYLOAD_MAX_CHARS = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", "20000000"))

MAX_SSE_BYTES = 10_000_000  # capture up to 10MB of SSE bodies for full logging
LOG_STREAM_PROGRESS_INTERVAL_MS = max(0, int(os.getenv("LOG_STREAM_PROGRESS_INTERVAL_MS", "1000")))
ENABLE_OPENAI_STREAM_COMPAT = os.getenv("ENABLE_OPENAI_STREAM_COMPAT", "1") == "1"


def _parse_captured_sse_text(sse_text: str) -> tuple[str, str | None, dict | None, int]:
    assistant_parts: list[str] = []
    finish_reason: str | None = None
    final_usage: dict | None = None
    event_count = 0
    buf = sse_text
    while True:
        m_sep = re.search(r"\r?\n\r?\n", buf)
        if not m_sep:
            break
        block = buf[:m_sep.start()]
        buf = buf[m_sep.end():]
        data_lines = []
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

def _usage_tokens(usage: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    if not isinstance(usage, dict):
        return (None, None, None)

    def _get_int(name: str) -> Optional[int]:
        try:
            value = usage.get(name)
            return int(value) if value is not None else None
        except Exception:
            return None

    return (_get_int("prompt_tokens"), _get_int("completion_tokens"), _get_int("total_tokens"))


def _contains_archived_context(messages: List[Dict[str, Any]]) -> bool:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str) and "[ARCHIVED_COMPACT_CONTEXT]" in content:
                return True
    return False


def _is_tool_orchestration_payload(payload: Dict[str, Any], messages: List[Dict[str, Any]]) -> bool:
    kind = classify_messages(messages)
    # Memory-management payloads should not carry archived compact context,
    # but tool-enabled chat / web-search requests still benefit from history compaction.
    return kind == "memory"


def _count_tokens_safe(messages: List[Dict[str, Any]]) -> int | None:
    try:
        return TOK.count_messages(messages)
    except Exception:
        return None


def _count_text_tokens_safe(text: str) -> int | None:
    try:
        return TOK.count_text(text)
    except Exception:
        return None


def _clamp_max_out_for_ctx(requested_max_tokens: Any, ctx_eff: int) -> int:
    requested = int(requested_max_tokens) if isinstance(requested_max_tokens, int) and requested_max_tokens > 0 else 900
    hard_cap = max(64, int(ctx_eff) - int(SAFETY_MARGIN_TOK) - 256)
    return max(64, min(requested, hard_cap))


def _try_cache_append_repack(
    *,
    req_id: str,
    messages: List[Dict[str, Any]],
    threshold: int,
    desired_start_idx: int,
    user_id: str = "",
    conv_id: str = "",
    pinned_head_n: int = 0,
) -> tuple[list[Dict[str, Any]] | None, int, str | None, object | None]:
    _sys_msg, non_system = split_messages(messages)
    if not SUMMARY_CACHE_ENABLED or not non_system:
        return None, -1, None, None

    fingerprint = conversation_fingerprint(messages=messages, user_id=user_id, conv_id=conv_id, n_head=SUMMARY_CACHE_FINGERPRINT_MSGS)
    entries = load_cache_entries(SUMMARY_CACHE_DIR, fingerprint, user_id=user_id, conv_id=conv_id)
    log("INFO", "summary_cache_lookup", req_id=req_id, fingerprint=fingerprint, candidates=len(entries))
    best, rejected = find_best_prefix_entry_with_reasons(entries, non_system, expected_start_idx=desired_start_idx)
    for item in rejected:
        log("INFO", "summary_cache_candidate_rejected", req_id=req_id, fingerprint=fingerprint, range=f"{item['start_idx']}..{item['end_idx']}", reason=item['reason'])
    if not best:
        log("INFO", "summary_cache_miss", req_id=req_id, fingerprint=fingerprint, expected_start_idx=desired_start_idx)
        return None, -1, fingerprint, None

    append_until_idx = choose_append_until_idx(
        tok=TOK,
        original=messages,
        summary_text=best.summary_text,
        covered_end_idx=best.end_idx,
        threshold=threshold,
        pinned_head_n=pinned_head_n,
    )
    repacked = build_messages_from_summary_prefix(
        messages,
        summary_text=best.summary_text,
        covered_end_idx=best.end_idx,
        append_until_idx=append_until_idx,
        pinned_head_n=pinned_head_n,
    )
    log(
        "INFO",
        "summary_cache_hit",
        req_id=req_id,
        fingerprint=fingerprint,
        range=f"{best.start_idx}..{best.end_idx}",
        appended_raw=max(0, append_until_idx - best.end_idx),
        final_last_idx=append_until_idx,
    )
    return repacked, append_until_idx, fingerprint, best


async def _config_watcher():
    """Background task to watch for config file changes."""
    from .logger import log_config_reload as log_reload_event, log_config_error
    from .config import check_config_reload, get_config_mtime

    while True:
        try:
            result = check_config_reload()
            if result:
                current = get_config_mtime() or 0.0
                log_reload_event(current - 1.0, current)
        except Exception as e:
            log_config_error(str(e))

        # Check every 2 seconds
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup: Initialize server logging and config watcher
    from .logger import setup_server_logging, log_config_reload as log_reload_event
    from .config import check_config_reload

    # Setup file-based server logging
    logger = setup_server_logging()

    # Initial config reload check (in case config changed before startup)
    if check_config_reload():
        log_reload_event(0.0, 0.0)

    # Start the config watcher background task
    asyncio.create_task(_config_watcher())

    try:
        yield
    finally:
        # Shutdown: Close HTTP client
        await close_http_client()


app = FastAPI(lifespan=lifespan)


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc):
    """Custom 404 handler that logs the request details."""
    from .logger import log_server_event
    
    # Extract client model from path if present
    client_model = "unknown"
    path = str(request.url.path)
    if "/chat/completions" in path:
        parts = path.split("/")
        if len(parts) >= 3:
            client_model = "/".join(parts[1:3])
    
    # Log the 404 with details
    log_server_event("ERROR", "Route not found - request dropped",
                     request_path=path,
                     client_model=client_model,
                     method=request.method)
    
    return JSONResponse(
        {"detail": "Not Found"},
        status_code=404
    )


@app.get("/metrics")
async def get_metrics():
    """Get system and conversation metrics"""
    return {
        "system": METRICS_COLLECTOR.get_system_metrics(),
        "summary_stats": METRICS_COLLECTOR.get_summary_statistics(),
        "conversation_count": len(METRICS_COLLECTOR.conversations),
    }


@app.get("/v1/models")
async def list_models():
    """List all available models with their context lengths.

    Returns one entry per route, including duplicates under different names.
    For routes with summarization enabled, reports max(ctx_len_main, ctx_len_summary).
    Excludes routes marked with @private decorator.
    """
    from keeprollming.routing import _UNSET
    
    # Get private routes to exclude
    from .config import get_private_routes
    private_routes = get_private_routes()

    models = []

    for route in USER_ROUTES:
        # Skip private routes (marked with @private decorator)
        if route.name in private_routes:
            continue
            
        settings = get_route_settings(route, route.name)

        # Get model context length
        model = settings["model"]
        summary_model = settings["summary_model"]
        is_summary_enabled = settings.get("summary_enabled", True)

        # Resolve ctx_len for main model using hierarchy
        # Note: MODELS_CONFIG is now empty since models are defined inline in routes
        route_with_ctx = Route(
            name=route.name,
            pattern=route.pattern,
            model=settings["model"],
            summary_model=summary_model,
            ctx_len=_UNSET if route.ctx_len is None else route.ctx_len,  # type: ignore
            max_tokens=_UNSET if route.max_tokens is None else route.max_tokens,  # type: ignore
        )

        main_ctx_len = resolve_route_settings(route_with_ctx, {}, DEFAULTS)[0]

        # If summarization enabled and summary model has different ctx_len, use max
        if is_summary_enabled and summary_model and summary_model != settings["model"]:
            route_summary = Route(
                name=route.name,
                pattern=route.pattern,
                model=summary_model,
                summary_model=None,
                ctx_len=_UNSET if route.ctx_len is None else route.ctx_len,  # type: ignore
                max_tokens=_UNSET if route.max_tokens is None else route.max_tokens,  # type: ignore
            )
            summary_ctx_len = resolve_route_settings(route_summary, {}, DEFAULTS)[0]
            ctx_len = max(main_ctx_len, summary_ctx_len)
        else:
            ctx_len = main_ctx_len

        models.append({
            "id": route.name,
            "object": "model",
            "owned_by": "orchestrator",
            "context_length": ctx_len,
        })

    return {"data": models}


@app.post("/v1/chat/completions")
async def chat_completions(req: Request) -> Response:
    req_id = os.urandom(6).hex()
    payload = await req.json()
    headers = dict(req.headers)
    
    # Track start time for metrics
    t_start = time.perf_counter()

    # Controllare se ci sono gli header necessari per il caching
    user_id = headers.get("x-librechat-user-id", "")
    conv_id = headers.get("x-librechat-conversation-id", "")
    msg_id = headers.get("x-librechat-message-id", "")
    parent_msg_id = headers.get("x-librechat-parent-message-id", "")

    # Se abbiamo gli header, li usiamo per il fingerprint
    has_headers = bool(user_id or conv_id)

    client_model = payload.get("model")

    # Log request entry with key details
    log(
        "INFO",
        "http_in",
        req_id=req_id,
        client_model=client_model,
        stream=payload.get("stream", False),
        max_tokens=payload.get("max_tokens"),
        message_count=len(payload.get("messages", [])),
        user_id=user_id or None,
        conv_id=conv_id or None,
    )

    if LOG_MODE == "DEBUG":
        log("INFO", "request_received", header=headers, req_id=req_id, body_json=snip_json(payload))

    client_model = payload.get("model")

    # Use new routing system to resolve route and backend model
    route, model = resolve_route(client_model)
    route_settings = get_route_settings(route, model)
    
    # Extract settings from route configuration
    upstream_model = route_settings["model"]
    summary_model = route_settings["summary_model"] or route_settings["model"]
    is_passthrough = route_settings["passthrough_enabled"]
    transform_reasoning_content = route_settings["transform_reasoning_content"]
    add_empty_content_when_reasoning_only = route_settings["add_empty_content_when_reasoning_only"]
    reasoning_placeholder = route_settings["reasoning_placeholder_content"]
    
    # Get route-specific upstream URL (if defined, otherwise use global default)
    route_upstream_url = route_settings.get("upstream_url") or UPSTREAM_BASE_URL
    route_headers = route_settings.get("upstream_headers", {})

    # Resolve ctx_len and max_tokens using 3-level hierarchy:
    # Route > Model > Defaults
    # Note: MODELS_CONFIG is now empty since models are defined inline in routes
    resolved_ctx_len, resolved_max_tokens = resolve_route_settings(route, {}, DEFAULTS)

    # Log routing decision and configuration
    log(
        "INFO",
        "route_resolved",
        req_id=req_id,
        client_model=client_model,
        resolved_route=route.name,
        model=model,
        upstream_model=upstream_model,
        summary_model=summary_model,
        passthrough_enabled=is_passthrough,
        ctx_len=resolved_ctx_len,
        max_tokens_default=resolved_max_tokens,
    )

    # Check for custom prompt in request
    custom_prompt_type = payload.get("summary_prompt_type")
    custom_prompt_text = payload.get("summary_prompt")

    # If we have a custom prompt text and no specific type, use it directly as the prompt type
    if custom_prompt_text and isinstance(custom_prompt_text, str) and not custom_prompt_type:
        custom_prompt_type = custom_prompt_text

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": {"message": "Invalid payload: messages must be a list"}}, status_code=400)

    # Calculate message length metrics for tracking
    first_message_length = 0
    last_message_length = 0
    avg_message_length = 0.0
    
    if isinstance(messages, list) and len(messages) > 0:
        total_chars = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                length = len(content)
                total_chars += length
                if msg.get("role") == "user":
                    last_message_length = max(last_message_length, length)
        
        first_message_length = len(messages[0].get("content", "")) if messages else 0
        avg_message_length = total_chars / len(messages) if messages else 0

    stream = bool(payload.get("stream", False))

    last_user = extract_last_user_text(messages)
    if LOG_MODE in {"BASIC", "BASIC_PLAIN"} and last_user:
        log("INFO", "conv_user", req_id=req_id, text=last_user)

    # Use resolved ctx_len instead of fetching from upstream
    ctx_eff = resolved_ctx_len

    max_tokens_req = payload.get("max_tokens")
    max_out = _clamp_max_out_for_ctx(max_tokens_req, ctx_eff)

    plan = should_summarise(
        tok=TOK,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )
    _sys_msg_plan, _non_system_plan = split_messages(messages)
    pinned_head_n = _pinned_head_count(_non_system_plan)

    did_summarize = False
    repacked_messages = messages
    skip_summary_for_tools = _is_tool_orchestration_payload(payload, messages)
    summary_tokens = 0

    if is_passthrough:
        log(
            "INFO",
            "summary_bypassed",
            req_id=req_id,
            reason="passthrough_model",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif skip_summary_for_tools:
        log(
            "INFO",
            "summary_bypassed",
            req_id=req_id,
            reason="memory_payload",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif not plan.should:
        log(
            "INFO",
            "summary_bypassed",
            req_id=req_id,
            reason=plan.reason,
            prompt_tok_est=plan.prompt_tok_est if plan.prompt_tok_est is not None else 0,
            threshold=plan.threshold if plan.threshold is not None else 0,
        )

    if (not is_passthrough) and (not skip_summary_for_tools) and plan.should:
        try:
            log(
                "INFO",
                "summary_needed",
                req_id=req_id,
                prompt_tok_est=plan.prompt_tok_est or 0,
                threshold=plan.threshold or 0,
                head_n=plan.head_n,
                tail_n=plan.tail_n,
                middle_count=plan.middle_count,
                summary_model=summary_model,
                repacked_tok_est=plan.repacked_tok_est,
                summary_mode=SUMMARY_MODE,
                pinned_head_n=plan.pinned_head_n,
            )

            if SUMMARY_MODE == "cache_append":
                cache_repacked, append_until_idx, fingerprint, cache_entry = _try_cache_append_repack(
                    req_id=req_id,
                    messages=messages,
                    threshold=plan.threshold or 0,
                    desired_start_idx=plan.head_n,
                    user_id=user_id,
                    conv_id=conv_id,
                    pinned_head_n=pinned_head_n,
                )
                _sys_msg, non_system = split_messages(messages)
                if cache_repacked is not None and append_until_idx >= len(non_system) - 1:
                    repacked_messages = cache_repacked
                    did_summarize = True
                else:
                    need_consolidate = SUMMARY_FORCE_CONSOLIDATE or (cache_repacked is not None and SUMMARY_CONSOLIDATE_WHEN_NEEDED)
                    if cache_repacked is None:
                        # No cache hit yet: use classic head/middle/tail summarization, then store the summarized middle in cache.
                        head_n = max(plan.head_n, pinned_head_n)
                        tail_n = plan.tail_n
                        n = len(non_system)
                        middle = non_system[head_n : n - tail_n] if (head_n + tail_n) < n else []
                        summary_text = await summarize_middle(middle, req_id=req_id, summary_model=summary_model, prompt_type=custom_prompt_type, lang_hint=custom_prompt_text or "italiano")
                        repacked_messages, _middle_used = build_repacked_messages(
                            messages,
                            summary_text=summary_text,
                            head_n=head_n,
                            tail_n=tail_n,
                            pinned_head_n=pinned_head_n,
                        )
                        did_summarize = True
                        summary_tokens = _count_tokens_safe(summary_text) or 0
                        if middle:
                            start_idx = head_n
                            end_idx = n - tail_n - 1
                            if is_summary_cacheable(summary_text):
                                entry = make_cache_entry(
                                    fingerprint=fingerprint or conversation_fingerprint(messages=messages, user_id=user_id, conv_id=conv_id, n_head=SUMMARY_CACHE_FINGERPRINT_MSGS),
                                    start_idx=start_idx,
                                    end_idx=end_idx,
                                    messages=non_system,
                                    summary_text=summary_text,
                                    summary_model=summary_model,
                                    token_estimate=_count_tokens_safe(repacked_messages) or 0,
                                    source_mode="cache_append_initial",
                                )
                                path = save_cache_entry(SUMMARY_CACHE_DIR, entry, user_id=user_id, conv_id=conv_id)
                                log("INFO", "summary_cache_save", req_id=req_id, range=f"{start_idx}..{end_idx}", path=str(path))
                            else:
                                log("INFO", "summary_cache_skip_save", req_id=req_id, reason="summary_not_cacheable", range=f"{start_idx}..{end_idx}")
                    elif need_consolidate and cache_entry is not None:
                        desired_end_idx = max(cache_entry.start_idx, len(non_system) - max(1, plan.tail_n) - 1)
                        new_messages = non_system[cache_entry.end_idx + 1 : desired_end_idx + 1] if desired_end_idx > cache_entry.end_idx else []
                        if not new_messages:
                            repacked_messages = cache_repacked or messages
                            did_summarize = cache_repacked is not None
                        else:
                            log("INFO", "summary_incremental_reuse", req_id=req_id, base_range=f"{cache_entry.start_idx}..{cache_entry.end_idx}", delta_range=f"{cache_entry.end_idx + 1}..{desired_end_idx}")
                            summary_text = await summarize_incremental(
                                cache_entry.summary_text,
                                new_messages,
                                req_id=req_id,
                                summary_model=summary_model,
                                prompt_type=custom_prompt_type
                            )
                            end_idx = desired_end_idx
                            repacked_messages = build_messages_from_summary_prefix(
                                messages,
                                summary_text=summary_text,
                                covered_end_idx=end_idx,
                                append_until_idx=len(non_system) - 1,
                                pinned_head_n=pinned_head_n,
                            )
                            did_summarize = True
                            summary_tokens = _count_tokens_safe(summary_text) or 0
                            if is_summary_cacheable(summary_text):
                                entry = make_cache_entry(
                                    fingerprint=fingerprint or conversation_fingerprint(messages=messages, user_id=user_id, conv_id=conv_id, n_head=SUMMARY_CACHE_FINGERPRINT_MSGS),
                                    start_idx=cache_entry.start_idx,
                                    end_idx=end_idx,
                                    messages=non_system,
                                    summary_text=summary_text,
                                    summary_model=summary_model,
                                    token_estimate=_count_tokens_safe(repacked_messages) or 0,
                                    source_mode="cache_append_consolidated",
                                )
                                path = save_cache_entry(SUMMARY_CACHE_DIR, entry, user_id=user_id, conv_id=conv_id)
                                log("INFO", "summary_consolidate", req_id=req_id, range=f"{cache_entry.start_idx}..{end_idx}")
                                log("INFO", "summary_cache_save", req_id=req_id, range=f"{cache_entry.start_idx}..{end_idx}", path=str(path))
                            else:
                                log("INFO", "summary_cache_skip_save", req_id=req_id, reason="summary_not_cacheable", range=f"{cache_entry.start_idx}..{end_idx}")
                    else:
                        repacked_messages = cache_repacked or messages
                        did_summarize = cache_repacked is not None
            else:
                _, non_system = (None, [m for m in messages if m.get("role") != "system"])
                head_n = plan.head_n
                tail_n = plan.tail_n
                n = len(non_system)
                middle = non_system[head_n : n - tail_n] if (head_n + tail_n) < n else []
                summary_text = await summarize_middle(middle, req_id=req_id, summary_model=summary_model, prompt_type=custom_prompt_type, lang_hint=custom_prompt_text or "italiano")
                repacked_messages, _middle_used = build_repacked_messages(
                    messages,
                    summary_text=summary_text,
                    head_n=plan.head_n,
                    tail_n=plan.tail_n,
                    pinned_head_n=plan.pinned_head_n,
                )
                did_summarize = True
                summary_tokens = _count_tokens_safe(summary_text) or 0

            repacked_messages = ensure_repacked_has_user_message(repacked_messages, messages)
            repacked_tok_est = TOK.count_messages(repacked_messages)
            log(
                "INFO",
                "repacked",
                req_id=req_id,
                did_summarize=did_summarize,
                repacked_msg_count=len(repacked_messages),
                repacked_tok_est=repacked_tok_est,
                head_n=plan.head_n,
                tail_n=plan.tail_n,
                pinned_head_n=plan.pinned_head_n,
            )
        except Exception as e:
            log("ERROR", "summary_failed_fallback_passthrough", req_id=req_id, err=str(e))
            repacked_messages = messages
            did_summarize = False

    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model
    upstream_payload["messages"] = repacked_messages

    prompt_tokens_for_log = None
    try:
        prompt_tokens_for_log = TOK.count_messages(repacked_messages)
    except Exception:
        prompt_tokens_for_log = None

    # Only add max_tokens to upstream if explicitly configured (not _UNSET)
    from .routing import _UNSET
    if DEFAULT_MAX_COMPLETION_TOKENS is not _UNSET:  # type: ignore
        max_tokens_upstream = max(64, int(ctx_eff) - int(prompt_tokens_for_log or 0) - int(SAFETY_MARGIN_TOK))
        requested_out = int(max_tokens_req) if isinstance(max_tokens_req, int) and max_tokens_req > 0 else DEFAULT_MAX_COMPLETION_TOKENS  # type: ignore
        adjusted_out = min(requested_out, max_tokens_upstream)
        upstream_payload["max_tokens"] = adjusted_out
        if adjusted_out < requested_out:
            log(
                "WARN",
                "max_tokens_clamped",
                req_id=req_id,
                requested=requested_out,
                adjusted=adjusted_out,
                ctx_len=ctx_eff,
                prompt_tokens=prompt_tokens_for_log,
                safety_margin=SAFETY_MARGIN_TOK,
            )

    req_summary = summarize_request_payload(upstream_payload)

    log(
        "INFO",
        "upstream_req_repacked",
        req_id=req_id,
        did_summarize=did_summarize,
        passthrough=is_passthrough,
        upstream_url=f"{route_upstream_url}/v1/chat/completions",
        prompt_tokens=prompt_tokens_for_log,
        **req_summary,
        adjusted_max_tokens=upstream_payload.get("max_tokens"),
        body_json=snip_json(upstream_payload),
    )

    url = f"{route_upstream_url}/v1/chat/completions"
    client = await http_client()

    # Resolve fallback chain up front so both streaming and non-streaming paths can safely reference it.
    fallback_attempts = []
    visited_models: set[str] = {upstream_model}

    # Log fallback chain availability with primary model
    if route.fallback_chain:
        log(
            "INFO",
            "fallback_chain_available",
            req_id=req_id,
            chain=route.fallback_chain,
            primary_model=upstream_model,
        )
        fallback_attempts = resolve_fallback_chain(route, upstream_model, req_id)

    if stream:
        async def _iter():
            t0 = time.perf_counter()
            ttft_ms: float | None = None
            captured = bytearray()

            # Reconstruct full assistant reply from streamed SSE events (OpenAI-compatible chunks).
            sse_buf = ""
            assistant_parts: list[str] = []
            final_usage: dict | None = None
            finish_reason: str | None = None
            upstream_model_seen: str | None = None
            stream_event_count = 0
            first_token_ts: float | None = None
            generated_tokens_est: int | None = 0
            last_progress_log_ms = 0.0
            r: httpx.Response | None = None
            # Store full reconstructed response body for logging
            reconstructed_response: dict = {
                "id": "",
                "object": "chat.completion",
                "created": 0,
                "model": "",
                "choices": [{}]
            }
            # Track accumulated tool_calls by index
            tool_calls_accumulator: dict[int, dict] = {}
            
            # Track if we need to transform reasoning_content -> content for compatibility
            needs_transformation = is_passthrough and (transform_reasoning_content or add_empty_content_when_reasoning_only)

            # Track whether we've seen regular content (not just reasoning_content) during streaming
            has_seen_regular_content: bool = False
            # Track whether we've already emitted an assistant role delta downstream.
            role_sent: bool = False
            
            try:
                async with client.stream("POST", url, json=upstream_payload, headers=route_headers) as resp:
                    r = resp
                    if resp.status_code >= 400:
                        err_bytes = await resp.aread()
                        err_text = err_bytes.decode("utf-8", errors="replace")
                        log(
                            "ERROR",
                            "upstream_http_error_stream",
                            req_id=req_id,
                            status=resp.status_code,
                            body=err_text[:500],
                            did_summarize=did_summarize,
                            passthrough=is_passthrough,
                        )
                        if fallback_attempts and upstream_model not in visited_models:
                            log(
                                "INFO",
                                "upstream_error_fallback_attempt",
                                req_id=req_id,
                                original_model=upstream_model,
                                err=f"HTTP {resp.status_code}",
                            )
                            for route_opt, fallback_model in fallback_attempts:
                                if fallback_model not in visited_models:
                                    log(
                                        "INFO",
                                        "fallback_to_next_model",
                                        req_id=req_id,
                                        from_model=upstream_model,
                                        to_model=fallback_model,
                                    )
                                    upstream_payload["model"] = fallback_model
                                    visited_models.add(fallback_model)
                                    try:
                                        async with client.stream("POST", url, json=upstream_payload, headers=route_headers) as resp_retry:
                                            r = resp_retry
                                            if resp_retry.status_code >= 400:
                                                retry_bytes = await resp_retry.aread()
                                                retry_text = retry_bytes.decode("utf-8", errors="replace")
                                                log_fallback_error(
                                                    req_id=req_id,
                                                    from_model=upstream_model,
                                                    to_model=fallback_model,
                                                    error_type="http_status_error",
                                                    err_msg=retry_text[:300],
                                                )
                                                visited_models.add(fallback_model)
                                                continue
                                            async for chunk in resp_retry.aiter_bytes():
                                                if not chunk:
                                                    continue
                                                yield chunk
                                            return
                                    except Exception as retry_err:
                                        log_fallback_error(
                                            req_id=req_id,
                                            from_model=upstream_model,
                                            to_model=fallback_model,
                                            error_type="http_status_error",
                                            err_msg=str(retry_err)[:300],
                                        )
                                        visited_models.add(fallback_model)
                        yield ("data: " + json.dumps({'error': {'message': 'Upstream error', 'details': err_text[:2000]}}) + "\n\n").encode("utf-8")
                        yield b"data: [DONE]\n\n"
                        return
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue

                        # DEBUG: log every single upstream SSE chunk as it arrives
                        if LOG_MODE == "DEBUG":
                            try:
                                await log_streaming_response(resp, chunk, elapsed_ms=None)
                            except Exception:
                                pass

                        # capture a snippet for logging (intentionally limited)
                        if len(captured) < MAX_SSE_BYTES:
                            take = min(len(chunk), MAX_SSE_BYTES - len(captured))
                            captured.extend(chunk[:take])

                        # If we need to transform reasoning_content, modify the chunk before yielding
                        transformed_chunk = chunk
                        if needs_transformation and LOG_MODE in {"DEBUG", "MEDIUM"}:
                            try:
                                sse_text = chunk.decode("utf-8", errors="replace")
                                if sse_text.startswith("data:"):
                                    data_content = sse_text[5:].strip()
                                    if data_content and data_content != "[DONE]":
                                        obj = json.loads(data_content)
                                        choices = obj.get("choices")
                                        if isinstance(choices, list) and choices:
                                            c0 = choices[0] if isinstance(choices[0], dict) else None
                                            if isinstance(c0, dict):
                                                delta = c0.get("delta")
                                                if isinstance(delta, dict):
                                                    has_reasoning = "reasoning_content" in delta
                                                    has_content = "content" in delta and delta["content"]
                                                    
                                                    if has_reasoning:
                                                        if transform_reasoning_content and not has_content:
                                                            # Transform reasoning_content -> content for OpenAI compatibility
                                                            transformed_delta = dict(delta)
                                                            transformed_delta["content"] = transformed_delta.pop("reasoning_content")
                                                            choices[0]["delta"] = transformed_delta
                                                            data_content = json.dumps(obj, separators=(",", ":"))
                                                            transformed_chunk = f"data: {data_content}\n\n".encode("utf-8")
                                                            if LOG_MODE == "DEBUG" and stream_event_count <= 5:
                                                                log(
                                                                    "INFO",
                                                                    "stream_transformed_reasoning_to_content",
                                                                    req_id=req_id,
                                                                    event_num=stream_event_count,
                                                                    original_keys=list(delta.keys()),
                                                                    method="transform",
                                                                )
                            except Exception as _t:
                                # If transformation fails, use original chunk
                                pass

                        # Feed SSE parser buffer (best-effort utf-8 decode)
                        try:
                            sse_buf += chunk.decode("utf-8", errors="replace")
                        except Exception:
                            pass

                        # Drain complete SSE blocks separated by blank line
                        while True:
                            m_sep = re.search(r"\r?\n\r?\n", sse_buf)
                            if not m_sep:
                                break
                            block = sse_buf[:m_sep.start()]
                            sse_buf = sse_buf[m_sep.end():]

                            data_lines = []
                            for line in block.splitlines():
                                line = line.rstrip("\r")
                                if line.startswith("data:"):
                                    data_lines.append(line[5:].lstrip())

                            if not data_lines:
                                continue

                            payload_sse = "\n".join(data_lines).strip()
                            if not payload_sse:
                                continue
                            if payload_sse == "[DONE]":
                                yield b"data: [DONE]\n\n"
                                continue

                            stream_event_count += 1
                            try:
                                obj = json.loads(payload_sse)
                            except Exception:
                                continue

                            if not isinstance(obj, dict):
                                continue

                            if ENABLE_OPENAI_STREAM_COMPAT:
                                # OpenAI compatibility: ensure delta.role = "assistant" is present on the
                                # first assistant/tool chunk actually emitted downstream.
                                emit_role_preface = False
                                if isinstance(obj.get("choices"), list) and obj.get("choices"):
                                    c0_emit = obj["choices"][0] if isinstance(obj["choices"][0], dict) else None
                                    if isinstance(c0_emit, dict):
                                        delta_emit = c0_emit.get("delta")
                                        if isinstance(delta_emit, dict):
                                            has_tool_calls_emit = bool(delta_emit.get("tool_calls"))
                                            has_content_emit = isinstance(delta_emit.get("content"), str) and bool(delta_emit.get("content"))
                                            has_reasoning_emit = isinstance(delta_emit.get("reasoning_content"), str) and bool(delta_emit.get("reasoning_content"))

                                            if not role_sent:
                                                # If the first meaningful chunk is tool-only, emit a synthetic
                                                # assistant-role preface before forwarding the real tool chunk.
                                                if has_tool_calls_emit and not has_content_emit and not has_reasoning_emit and "role" not in delta_emit:
                                                    emit_role_preface = True
                                                else:
                                                    delta_emit["role"] = delta_emit.get("role") or "assistant"
                                                    role_sent = True
                                                    obj["choices"][0]["delta"] = delta_emit

                                if emit_role_preface:
                                    role_chunk_obj = {
                                        "id": obj.get("id"),
                                        "object": obj.get("object") or "chat.completion.chunk",
                                        "created": obj.get("created"),
                                        "model": obj.get("model"),
                                        "choices": [
                                            {
                                                "index": 0,
                                                "delta": {"role": "assistant"},
                                                "finish_reason": None,
                                            }
                                        ],
                                    }
                                    yield ("data: " + json.dumps(role_chunk_obj, separators=(",", ":")) + "\n\n").encode("utf-8")
                                    role_sent = True

                                payload_out = json.dumps(obj, separators=(",", ":"))
                                yield ("data: " + payload_out + "\n\n").encode("utf-8")
                            else:
                                yield chunk

                            # Build reconstructed_response with basic fields
                            if isinstance(obj.get("id"), str):
                                reconstructed_response["id"] = obj["id"]
                            if isinstance(obj.get("object"), str):
                                reconstructed_response["object"] = obj["object"]
                            if isinstance(obj.get("created"), (int, float)):
                                reconstructed_response["created"] = obj["created"]
                            if isinstance(obj.get("model"), str):
                                reconstructed_response["model"] = obj["model"]

                            # Track tool_calls from this chunk
                            if isinstance(obj.get("choices"), list):
                                for choice in obj["choices"]:
                                    if isinstance(choice, dict) and "index" in choice:
                                        idx = choice["index"]
                                        delta = choice.get("delta", {})
                                        if isinstance(delta, dict) and "tool_calls" in delta:
                                            if idx not in tool_calls_accumulator:
                                                tool_calls_accumulator[idx] = {}
                                            # Merge tool_calls deltas
                                            for tc_delta in delta["tool_calls"]:
                                                if isinstance(tc_delta, dict) and "index" in tc_delta:
                                                    tc_idx = tc_delta["index"]
                                                    if tc_idx not in tool_calls_accumulator[idx]:
                                                        tool_calls_accumulator[idx][tc_idx] = {}
                                                    # Merge each field
                                                    for tc_key, tc_value in tc_delta.items():
                                                        if tc_key == "function" and isinstance(tc_value, dict):
                                                            # Handle nested function fields
                                                            if "function" not in tool_calls_accumulator[idx][tc_idx]:
                                                                tool_calls_accumulator[idx][tc_idx]["function"] = {}
                                                            for func_key, func_value in tc_value.items():
                                                                if func_key in tool_calls_accumulator[idx][tc_idx]["function"]:
                                                                    if isinstance(tool_calls_accumulator[idx][tc_idx]["function"][func_key], str) and isinstance(func_value, str):
                                                                        tool_calls_accumulator[idx][tc_idx]["function"][func_key] += func_value
                                                                    else:
                                                                        tool_calls_accumulator[idx][tc_idx]["function"][func_key] = func_value
                                                                else:
                                                                    tool_calls_accumulator[idx][tc_idx]["function"][func_key] = func_value
                                                        elif tc_key != "index":
                                                            if isinstance(tool_calls_accumulator[idx][tc_idx].get(tc_key), str) and isinstance(tc_value, str):
                                                                tool_calls_accumulator[idx][tc_idx][tc_key] += tc_value
                                                            else:
                                                                tool_calls_accumulator[idx][tc_idx][tc_key] = tc_value

                            # Track finish_reason from choices
                            if isinstance(obj.get("choices"), list):
                                for choice in obj["choices"]:
                                    if isinstance(choice, dict) and "index" in choice:
                                        idx = choice["index"]
                                        finish_reason = choice.get("finish_reason")
                                        if finish_reason:
                                            if len(reconstructed_response["choices"]) <= idx:
                                                reconstructed_response["choices"].append({})
                                            reconstructed_response["choices"][idx]["finish_reason"] = finish_reason

                            # OpenAI compatibility: inject role into reconstructed_response delta
                            if ENABLE_OPENAI_STREAM_COMPAT and isinstance(obj.get("choices"), list) and not role_sent:
                                c0 = obj["choices"][0] if isinstance(obj["choices"][0], dict) else None
                                if isinstance(c0, dict):
                                    delta = c0.get("delta", {})
                                    if isinstance(delta, dict):
                                        # Inject role into reconstructed_response
                                        if "delta" not in reconstructed_response["choices"][0]:
                                            reconstructed_response["choices"][0]["delta"] = {}
                                        reconstructed_response["choices"][0]["delta"]["role"] = "assistant"
                                        role_sent = True
                                        if LOG_MODE == "DEBUG":
                                            log("INFO", "role_injected_into_reconstructed_response", req_id=req_id, event_num=stream_event_count)

                            if upstream_model_seen is None and isinstance(obj.get("model"), str):
                                upstream_model_seen = obj.get("model")

                            if isinstance(obj.get("usage"), dict):
                                final_usage = obj.get("usage")

                            choices = obj.get("choices")
                            if isinstance(choices, list) and choices:
                                c0 = choices[0] if isinstance(choices[0], dict) else None
                                if isinstance(c0, dict):
                                    delta = c0.get("delta")
                                    new_text_piece = False
                                    
                                    # DEBUG: Log every chunk for first 10 events to see what's happening
                                    if LOG_MODE == "DEBUG" and stream_event_count <= 10:
                                        log(
                                            "INFO",
                                            "stream_chunk_debug",
                                            req_id=req_id,
                                            event_num=stream_event_count,
                                            has_delta=bool(delta),
                                            delta_keys=list(delta.keys()) if isinstance(delta, dict) else [],
                                        )
                                    
                                    if isinstance(delta, dict):
                                        piece = delta.get("content")
                                        if isinstance(piece, str) and piece:
                                            assistant_parts.append(piece)
                                            has_seen_regular_content = True
                                            new_text_piece = True
                                        
                                        # Log tool_calls or function_call even when no content
                                        # Handle reasoning_content (Qwen3.5 thinking tokens)
                                        reasoning = delta.get("reasoning_content")
                                        if isinstance(reasoning, str) and reasoning:
                                            # Include reasoning content in the response
                                            assistant_parts.append(reasoning)
                                            new_text_piece = True

                                        if not new_text_piece and LOG_MODE in {"DEBUG", "MEDIUM"}:
                                            tool_calls = delta.get("tool_calls")
                                            function_call = delta.get("function_call")
                                            if tool_calls or function_call:
                                                log(
                                                    "INFO",
                                                    "stream_tool_response",
                                                    req_id=req_id,
                                                    event_num=stream_event_count,
                                                    tool_calls=tool_calls,
                                                    function_call=function_call,
                                                )
                                    msg_obj = c0.get("message")
                                    if isinstance(msg_obj, dict):
                                        piece = msg_obj.get("content")
                                        if isinstance(piece, str) and piece:
                                            assistant_parts.append(piece)
                                            has_seen_regular_content = True
                                            new_text_piece = True

                                    # Log when we have choices but no content captured
                                    if not new_text_piece and LOG_MODE in {"DEBUG", "MEDIUM"}:
                                        has_tool_calls = bool(delta.get("tool_calls")) or bool(c0.get("tool_calls"))
                                        has_function_call = bool(delta.get("function_call")) or bool(c0.get("function_call"))
                                        
                                        # Log all delta keys for debugging
                                        log(
                                            "INFO",
                                            "stream_non_text_response",
                                            req_id=req_id,
                                            event_num=stream_event_count,
                                            has_tool_calls=has_tool_calls,
                                            has_function_call=has_function_call,
                                            delta_keys=list(delta.keys()) if isinstance(delta, dict) else [],
                                            tool_calls=c0.get("tool_calls"),
                                            function_call=c0.get("function_call"),
                                            raw_delta=delta,
                                        )

                                    if new_text_piece:
                                        now_perf = time.perf_counter()
                                        if first_token_ts is None:
                                            first_token_ts = now_perf
                                            ttft_ms = (now_perf - t0) * 1000.0
                                        try:
                                            generated_tokens_est = TOK.count_text("".join(assistant_parts))
                                        except Exception:
                                            generated_tokens_est = generated_tokens_est or None

                                        if LOG_STREAM_PROGRESS_INTERVAL_MS > 0:
                                            elapsed_since_start_ms = (now_perf - t0) * 1000.0
                                            should_emit_progress = (
                                                last_progress_log_ms <= 0.0
                                                or (elapsed_since_start_ms - last_progress_log_ms) >= LOG_STREAM_PROGRESS_INTERVAL_MS
                                            )
                                            if should_emit_progress:
                                                gen_elapsed_s = max(0.001, now_perf - (first_token_ts or now_perf))
                                                tps_live = None
                                                if isinstance(generated_tokens_est, int) and generated_tokens_est > 0:
                                                    tps_live = round(generated_tokens_est / gen_elapsed_s, 3)
                                                log(
                                                    "INFO",
                                                    "stream_progress",
                                                    req_id=req_id,
                                                    upstream_model=(upstream_model_seen or upstream_payload.get("model")),
                                                    elapsed_ms=round(elapsed_since_start_ms, 3),
                                                    ttft_ms=(round(ttft_ms, 3) if ttft_ms is not None else None),
                                                    generated_tokens_est=generated_tokens_est,
                                                    tps_live=tps_live,
                                                    event_count=stream_event_count,
                                                )
                                                last_progress_log_ms = elapsed_since_start_ms
                                    fr = c0.get("finish_reason")
                                    if isinstance(fr, str) and fr:
                                        finish_reason = fr

                        # If add_empty_content_when_reasoning_only is enabled and we haven't seen any regular content,
                        # inject an empty content chunk at the end of the stream
                        if add_empty_content_when_reasoning_only and not has_seen_regular_content and finish_reason is not None:
                            # Create a final delta with empty content to satisfy OpenAI clients
                            final_chunk_obj = {
                                "id": obj.get("id"),
                                "created": obj.get("created"),
                                "model": obj.get("model"),
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": reasoning_placeholder or ""},
                                        "finish_reason": finish_reason,
                                    }
                                ],
                            }
                            if final_usage:
                                final_chunk_obj["usage"] = final_usage
                            # Re-encode the (possibly modified) chunk for downstream
                            payload = json.dumps(final_chunk_obj, separators=(",", ":"))
                            yield (f"data: {payload}\n\n").encode("utf-8")
                            if LOG_MODE == "DEBUG" and not has_seen_regular_content:
                                log(
                                    "INFO",
                                    "stream_added_empty_content_at_end",
                                    req_id=req_id,
                                    placeholder_used=reasoning_placeholder or "(empty)",
                                )

            except Exception as e:
                # Use sys.exc_info() to get exception type at runtime (needed for nested generators)
                exc_type = sys.exc_info()[1].__class__
                if exc_type is httpx.ConnectError:
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    log_connection_error(
                        req_id=req_id, error_type="connection_failed",
                        upstream_url=url, model=upstream_model,
                        elapsed_ms=elapsed_ms, err=str(e)[:300],
                    )
                    if fallback_attempts and upstream_model not in visited_models:
                        log("INFO", "connection_error_fallback_attempt", req_id=req_id,
                            original_model=upstream_model, upstream_url=url)
                        for route_opt, fallback_model in fallback_attempts:
                            if fallback_model not in visited_models:
                                log("INFO", "fallback_to_next_model", req_id=req_id,
                                    from_model=upstream_model, to_model=fallback_model)
                                upstream_payload["model"] = fallback_model
                                visited_models.add(fallback_model)
                                try:
                                    async with client.stream("POST", url, json=upstream_payload, headers=route_headers) as resp_retry:
                                        r = resp_retry
                                        if resp_retry.status_code >= 400:
                                            retry_bytes = await resp_retry.aread()
                                            retry_text = retry_bytes.decode("utf-8", errors="replace")
                                            log_fallback_error(req_id=req_id, from_model=upstream_model,
                                                to_model=fallback_model, error_type="http_status_error",
                                                err_msg=retry_text[:300])
                                            visited_models.add(fallback_model)
                                            continue
                                        async for chunk in resp_retry.aiter_bytes():
                                            if not chunk: continue
                                            yield chunk
                                        return
                                except Exception as retry_err:
                                    log_fallback_error(req_id=req_id, from_model=upstream_model,
                                        to_model=fallback_model, error_type="connection_failed",
                                        err_msg=str(retry_err)[:300])
                                    visited_models.add(fallback_model)
                        yield (f"data: {json.dumps({'error': {'message': 'Connection failed - all upstreams unreachable'}})}\n\n").encode("utf-8")
                        yield b"data: [DONE]\n\n"
                    else:
                        yield (f"data: {json.dumps({'error': {'message': 'Connection failed', 'details': str(e)[:200]}})}\n\n").encode("utf-8")
                        yield b"data: [DONE]\n\n"
                elif exc_type is httpx.ConnectTimeout:
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    log_connection_error(
                        req_id=req_id, error_type="connection_timeout",
                        upstream_url=url, model=upstream_model,
                        elapsed_ms=elapsed_ms, err=str(e)[:300],
                    )
                    if fallback_attempts and upstream_model not in visited_models:
                        log("INFO", "timeout_error_fallback_attempt", req_id=req_id,
                            original_model=upstream_model, upstream_url=url)
                        for route_opt, fallback_model in fallback_attempts:
                            if fallback_model not in visited_models:
                                log("INFO", "fallback_to_next_model", req_id=req_id,
                                    from_model=upstream_model, to_model=fallback_model)
                                upstream_payload["model"] = fallback_model
                                visited_models.add(fallback_model)
                                try:
                                    async with client.stream("POST", url, json=upstream_payload, headers=route_headers) as resp_retry:
                                        r = resp_retry
                                        if resp_retry.status_code >= 400:
                                            retry_bytes = await resp_retry.aread()
                                            retry_text = retry_bytes.decode("utf-8", errors="replace")
                                            log_fallback_error(req_id=req_id, from_model=upstream_model,
                                                to_model=fallback_model, error_type="http_status_error",
                                                err_msg=retry_text[:300])
                                            visited_models.add(fallback_model)
                                            continue
                                        async for chunk in resp_retry.aiter_bytes():
                                            if not chunk: continue
                                            yield chunk
                                        return
                                except Exception as retry_err:
                                    log_fallback_error(req_id=req_id, from_model=upstream_model,
                                        to_model=fallback_model, error_type="connection_timeout",
                                        err_msg=str(retry_err)[:300])
                                    visited_models.add(fallback_model)
                        yield (f"data: {json.dumps({'error': {'message': 'Connection timeout - all upstreams timed out'}})}\n\n").encode("utf-8")
                        yield b"data: [DONE]\n\n"
                    else:
                        yield (f"data: {json.dumps({'error': {'message': 'Connection timeout', 'details': str(e)[:200]}})}\n\n").encode("utf-8")
                        yield b"data: [DONE]\n\n"
                elif exc_type is httpx.HTTPStatusError:
                    err_text = ""
                    try:
                        err_bytes = await e.response.aread()
                        err_text = err_bytes.decode("utf-8", errors="replace")
                    except Exception as read_err:
                        err_text = f"<unable to read upstream error body: {read_err}>"

                    log(
                        "ERROR",
                        "upstream_http_error_stream",
                        req_id=req_id,
                        status=e.response.status_code,
                        body=err_text[:500],
                        did_summarize=did_summarize,
                        passthrough=is_passthrough,
                    )
                    if fallback_attempts and upstream_model not in visited_models:
                        log(
                            "INFO",
                            "upstream_error_fallback_attempt",
                            req_id=req_id,
                            original_model=upstream_model,
                            err=str(e),
                        )
                        for route_opt, fallback_model in fallback_attempts:
                            if fallback_model not in visited_models:
                                log(
                                    "INFO",
                                    "fallback_to_next_model",
                                    req_id=req_id,
                                    from_model=upstream_model,
                                    to_model=fallback_model,
                                )
                                upstream_payload["model"] = fallback_model
                                visited_models.add(fallback_model)
                                try:
                                    async with client.stream("POST", url, json=upstream_payload, headers=route_headers) as resp_retry:
                                        r = resp_retry
                                        if resp_retry.status_code >= 400:
                                            retry_bytes = await resp_retry.aread()
                                            retry_text = retry_bytes.decode("utf-8", errors="replace")
                                            log(
                                                "WARN",
                                                "fallback_retry_failed",
                                                req_id=req_id,
                                                from_model=upstream_model,
                                                to_model=fallback_model,
                                                err=retry_text[:300],
                                            )
                                            visited_models.add(fallback_model)
                                            continue
                                        async for chunk in resp_retry.aiter_bytes():
                                            if not chunk:
                                                continue
                                            yield chunk
                                    return
                                except Exception as retry_err:
                                    log(
                                        "WARN",
                                        "fallback_retry_failed",
                                        req_id=req_id,
                                        from_model=upstream_model,
                                        to_model=fallback_model,
                                        err=str(retry_err),
                                    )
                                    visited_models.add(fallback_model)

                    err_payload = {
                        "error": {
                            "message": "Upstream error",
                            "details": err_text[:2000],
                        }
                    }
                    yield ("data: " + json.dumps(err_payload, separators=(",", ":")) + "\n\n").encode("utf-8")
                    yield b"data: [DONE]\n\n"
                else:
                    raise

                    yield b"data: [DONE]\n\n"
            except Exception as e:
                log(
                    "ERROR",
                    "upstream_stream_exception",
                    req_id=req_id,
                    err=str(e),
                    did_summarize=did_summarize,
                    passthrough=is_passthrough,
                )
                yield (f"data: {json.dumps({'error': {'message': 'Upstream stream exception', 'details': str(e)}})}\n\n").encode("utf-8")
                yield b"data: [DONE]\n\n"
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                # If we did receive a response object, log the captured SSE snippet.
                if r is not None:
                    try:
                        await log_streaming_response(r, bytes(captured), elapsed_ms=elapsed_ms)
                    except Exception as _e:
                        # never let logging break streaming
                        log("WARN", "stream_log_failed", req_id=req_id, err=str(_e))

                # NEW: reconstructed full assistant message (clear log, not snipped)
                try:
                    full_text = "".join(assistant_parts).strip()
                    if (not full_text or finish_reason is None or final_usage is None) and captured:
                        parsed_text, parsed_finish, parsed_usage, parsed_events = _parse_captured_sse_text(captured.decode("utf-8", errors="replace"))
                        if parsed_text and not full_text:
                            full_text = parsed_text
                        if parsed_finish and finish_reason is None:
                            finish_reason = parsed_finish
                        if parsed_usage and final_usage is None:
                            final_usage = parsed_usage
                        if parsed_events and stream_event_count == 0:
                            stream_event_count = parsed_events
                    model_for_metrics = (upstream_model_seen or upstream_payload.get("model"))
                    prompt_tokens_u, completion_tokens_u, total_tokens_u = _usage_tokens(final_usage)
                    completion_tokens_source = "usage"
                    
                    
                    if completion_tokens_u is None and full_text:
                        completion_tokens_u = _count_text_tokens_safe(full_text)
                        completion_tokens_source = "estimated_text" if completion_tokens_u is not None else "missing"
                    elif completion_tokens_u is None:
                        completion_tokens_source = "missing"
                    perf_entry = record_request_performance(
                        model=model_for_metrics or "unknown",
                        route_name=route.name,
                        route_hierarchy=route._route_hierarchy,
                        req_id=req_id,
                        stream=True,
                        elapsed_ms=elapsed_ms,
                        ttft_ms=ttft_ms,
                        completion_tokens=completion_tokens_u,
                        prompt_tokens=prompt_tokens_for_log or prompt_tokens_u,
                        total_tokens=total_tokens_u,
                        finish_reason=finish_reason,
                        did_summarize=did_summarize,
                        passthrough=is_passthrough,
                        completion_tokens_source=completion_tokens_source,
                    )
                    
                    # Collect conversation metrics for streaming response
                    elapsed_total_ms = (time.perf_counter() - t_start) * 1000.0
                    
                    record_conversation_metrics(
                        conversation_id=conv_id or req_id,
                        user_id=user_id,
                        model_used=model_for_metrics or upstream_model,
                        prompt_tokens=prompt_tokens_u or 0,
                        completion_tokens=completion_tokens_u or 0,
                        total_tokens=total_tokens_u or 0,
                        summary_used=did_summarize,
                        summary_tokens=summary_tokens,
                        context_length=ctx_eff,
                        elapsed_time_ms=elapsed_total_ms,
                        request_count=len(messages) if isinstance(messages, list) else 0,
                        first_message_length=first_message_length,
                        last_message_length=last_message_length,
                        avg_message_length=avg_message_length,
                        summary_decision_reason=plan.reason if not is_passthrough and not skip_summary_for_tools else "passthrough"
                    )

                    # Add accumulated tool_calls to reconstructed_response
                    if tool_calls_accumulator and reconstructed_response["choices"]:
                        final_tool_calls = []
                        for idx in sorted(tool_calls_accumulator.keys()):
                            tc_dict = tool_calls_accumulator[idx]
                            for tc_idx in sorted(tc_dict.keys()):
                                final_tool_calls.append({
                                    "index": tc_idx,
                                    **tc_dict[tc_idx]
                                })
                        if final_tool_calls:
                            if len(reconstructed_response["choices"]) <= 0:
                                reconstructed_response["choices"] = [{}]
                            reconstructed_response["choices"][0]["tool_calls"] = final_tool_calls
                            # Preserve existing delta and merge tool_calls into it
                            if "delta" not in reconstructed_response["choices"][0]:
                                reconstructed_response["choices"][0]["delta"] = {}
                            # Add tool_calls to delta without overwriting existing fields like role
                            if "tool_calls" not in reconstructed_response["choices"][0]["delta"]:
                                reconstructed_response["choices"][0]["delta"]["tool_calls"] = final_tool_calls
                            # Ensure role is present (double-check)
                            if "role" not in reconstructed_response["choices"][0]["delta"]:
                                reconstructed_response["choices"][0]["delta"]["role"] = "assistant"

                    if LOG_MODE == "DEBUG":
                        log(
                            "INFO",
                            "response_stream_reconstructed",
                            req_id=req_id,
                            url=url,
                            upstream_model=model_for_metrics,
                            elapsed_ms=elapsed_ms,
                            did_summarize=did_summarize,
                            passthrough=is_passthrough,
                            finish_reason=finish_reason,
                            usage=final_usage,
                            event_count=stream_event_count,
                            ttft_ms=perf_entry.get("ttft_ms"),
                            tps=perf_entry.get("tps"),
                            completion_tokens_source=completion_tokens_source,
                            completion_tokens=completion_tokens_u,
                            assistant_text=full_text,
                            response_body=reconstructed_response,
                        )
                        # Log the final response that will be sent downstream
                        log(
                            "INFO",
                            "response_sent_downstream",
                            req_id=req_id,
                            response_body=reconstructed_response,
                        )
                    elif LOG_MODE == "MEDIUM":
                        log(
                            "INFO",
                            "response_stream_reconstructed",
                            req_id=req_id,
                            upstream_model=model_for_metrics,
                            elapsed_ms=elapsed_ms,
                            finish_reason=finish_reason,
                            usage=final_usage,
                            event_count=stream_event_count,
                            ttft_ms=perf_entry.get("ttft_ms"),
                            tps=perf_entry.get("tps"),
                            completion_tokens_source=completion_tokens_source,
                            completion_tokens=completion_tokens_u,
                            assistant_text=_snip_obj_active(full_text, LOG_SNIP_CHARS),
                        )
                    else:
                        log(
                            "INFO",
                            "response_stream_reconstructed",
                            req_id=req_id,
                            upstream_model=model_for_metrics,
                            elapsed_ms=elapsed_ms,
                            usage=final_usage,
                            finish_reason=finish_reason,
                            ttft_ms=perf_entry.get("ttft_ms"),
                            tps=perf_entry.get("tps"),
                            completion_tokens_source=completion_tokens_source,
                            completion_tokens=completion_tokens_u,
                            assistant_text=_snip_obj_active(full_text, BASIC_SNIP_CHARS),
                        )

                    # Log streaming request completion summary
                    log(
                        "INFO",
                        "request_completed_streaming",
                        req_id=req_id,
                        status=200,
                        elapsed_ms=elapsed_ms,
                        did_summarize=did_summarize,
                        passthrough=is_passthrough,
                        prompt_tokens=prompt_tokens_for_log or prompt_tokens_u,
                        completion_tokens=completion_tokens_u or 0,
                        total_tokens=total_tokens_u or 0,
                        finish_reason=finish_reason,
                        event_count=stream_event_count,
                    )
                except Exception as _e:
                    # Log streaming request completion with exception error
                    log(
                        "ERROR",
                        "request_completed_streaming_error",
                        req_id=req_id,
                        status=502,
                        elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                        did_summarize=did_summarize,
                        passthrough=is_passthrough,
                        error_type="stream_reconstruction_error",
                    )
                    log("WARN", "stream_reconstruct_log_failed", req_id=req_id, err=str(_e))

        log("INFO", "upstream_stream_start", req_id=req_id, did_summarize=did_summarize, passthrough=is_passthrough)
        
        # Log streaming response start
        log(
            "INFO",
            "request_started_streaming",
            req_id=req_id,
            upstream_url=url,
            model=upstream_model,
            did_summarize=did_summarize,
            passthrough=is_passthrough,
        )
        
        headers = {
            "content-type": "text/event-stream; charset=utf-8",
            "cache-control": "no-cache",
            "connection": "keep-alive",
        }
        return StreamingResponse(_iter(), headers=headers)

    t0 = time.time()
    
    # Track TTFT for non-streaming requests (estimate based on response timing)
    ttft_ms_non_stream: float | None = None
    
    # Track visited models for fallback chain (non-streaming)
    visited_models_non_stream: set = {upstream_model} if not route.fallback_chain else set([upstream_model])
    
    try:
        r = await client.post(url, json=upstream_payload, headers=route_headers)
        
        # If error and we have fallback options, retry with next model
        while r.status_code >= 400 and fallback_attempts:
            log(
                "ERROR",
                "upstream_http_error_fallback",
                req_id=req_id,
                status=r.status_code,
                body=r.text[:500],
                did_summarize=did_summarize,
                passthrough=is_passthrough,
            )
            
            # Find next fallback option that hasn't been tried
            for route_opt, fallback_model in fallback_attempts:
                if fallback_model not in visited_models_non_stream:
                    log(
                        "INFO",
                        "fallback_to_next_model_sync",
                        req_id=req_id,
                        from_model=upstream_model,
                        to_model=fallback_model,
                    )
                    
                    # Update payload with new model
                    upstream_payload["model"] = fallback_model
                    visited_models_non_stream.add(fallback_model)
                    
                    # Retry the request with the fallback model
                    try:
                        r = await client.post(url, json=upstream_payload, headers=route_headers)
                        
                        if r.status_code < 400:
                            log(
                                "INFO",
                                "fallback_success",
                                req_id=req_id,
                                from_model=upstream_model,
                                to_model=fallback_model,
                            )
                            break  # Success - exit retry loop
                    except Exception as retry_err:
                        log(
                            "WARN",
                            "fallback_retry_failed_sync",
                            req_id=req_id,
                            from_model=upstream_model,
                            to_model=fallback_model,
                            err=str(retry_err),
                        )
                        visited_models_non_stream.add(fallback_model)
                    else:
                        # Continue loop to check status code again
                        continue
                    
                    break  # Break inner loop, continue outer while
            
            # If still error after all fallbacks, return error
            if r.status_code >= 400 and not any(
                model in visited_models_non_stream 
                for _, model in fallback_attempts
            ):
                log(
                    "ERROR",
                    "upstream_http_error_all_fallbacks_exhausted",
                    req_id=req_id,
                    status=r.status_code,
                    body=r.text[:500],
                    did_summarize=did_summarize,
                    passthrough=is_passthrough,
                )
                return JSONResponse({"error": {"message": "Upstream error", "details": r.text}}, status_code=r.status_code)
        
        elapsed_ms = (time.time() - t0) * 1000.0
        
        # Estimate TTFT for non-streaming: assume ~30% of total time spent on prompt processing
        if ttft_ms_non_stream is None and elapsed_ms > 0:
            ttft_ms_non_stream = elapsed_ms * 0.3
        
        if r.status_code >= 400:
            log(
                "ERROR",
                "upstream_http_error",
                req_id=req_id,
                status=r.status_code,
                body=r.text[:800],
                did_summarize=did_summarize,
                passthrough=is_passthrough,
            )
            return JSONResponse({"error": {"message": "Upstream error", "details": r.text}}, status_code=r.status_code)

        data = r.json()
        resp_summary = summarize_response_payload(data)
        usage = data.get("usage") if isinstance(data, dict) else None
        prompt_tokens_u, completion_tokens_u, total_tokens_u = _usage_tokens(usage)
        completion_tokens_source = "usage"
        if completion_tokens_u is None and isinstance(data, dict):
            try:
                choice0 = (data.get("choices") or [{}])[0]
                msg = choice0.get("message") if isinstance(choice0, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, str) and content:
                    completion_tokens_u = _count_text_tokens_safe(content)
                    completion_tokens_source = "estimated_text" if completion_tokens_u is not None else "missing"
                else:
                    completion_tokens_source = "missing"
            except Exception:
                completion_tokens_source = "missing"
        elif completion_tokens_u is None:
            completion_tokens_source = "missing"
        perf_entry = record_request_performance(
            model=str(data.get("model") or upstream_model),
            route_name=route.name,
            route_hierarchy=route._route_hierarchy,
            req_id=req_id,
            stream=False,
            elapsed_ms=elapsed_ms,
            ttft_ms=ttft_ms_non_stream,
            completion_tokens=completion_tokens_u,
            prompt_tokens=prompt_tokens_for_log or prompt_tokens_u,
            total_tokens=total_tokens_u,
            finish_reason=(data.get("choices") or [{}])[0].get("finish_reason") if isinstance(data, dict) and isinstance(data.get("choices"), list) and data.get("choices") else None,
            did_summarize=did_summarize,
            passthrough=is_passthrough,
            completion_tokens_source=completion_tokens_source,
        )
        
        # Collect conversation metrics
        elapsed_total_ms = (time.perf_counter() - t_start) * 1000.0
        
        record_conversation_metrics(
            conversation_id=conv_id or req_id,
            user_id=user_id,
            model_used=str(data.get("model") or upstream_model),
            prompt_tokens=prompt_tokens_u or 0,
            completion_tokens=completion_tokens_u or 0,
            total_tokens=total_tokens_u or 0,
            summary_used=did_summarize,
            summary_tokens=summary_tokens,
            context_length=ctx_eff,
            elapsed_time_ms=elapsed_total_ms,
            request_count=len(messages) if isinstance(messages, list) else 0,
            first_message_length=first_message_length,
            last_message_length=last_message_length,
            avg_message_length=avg_message_length,
            summary_decision_reason=plan.reason if not is_passthrough and not skip_summary_for_tools else "passthrough"
        )
        if LOG_MODE == "DEBUG":
            log(
                "INFO",
                "http_out",
                req_id=req_id,
                status=200,
                elapsed_ms=round(elapsed_ms, 2),
                did_summarize=did_summarize,
                passthrough=is_passthrough,
                usage=data.get("usage"),
                tps=perf_entry.get("tps"),
                ttft_ms=perf_entry.get("ttft_ms"),
                completion_tokens_source=completion_tokens_source,
                completion_tokens=completion_tokens_u,
                data=data,
            )
        elif LOG_MODE == "MEDIUM":
            log(
                "INFO",
                "http_out",
                req_id=req_id,
                status=200,
                elapsed_ms=round(elapsed_ms, 2),
                usage=data.get("usage"),
                tps=perf_entry.get("tps"),
                ttft_ms=perf_entry.get("ttft_ms"),
                completion_tokens_source=completion_tokens_source,
                completion_tokens=completion_tokens_u,
                data=_snip_obj_active(data, LOG_SNIP_CHARS),
            )
        else:
            log(
                "INFO",
                "http_out",
                req_id=req_id,
                status=200,
                elapsed_ms=round(elapsed_ms, 2),
                tps=perf_entry.get("tps"),
                ttft_ms=perf_entry.get("ttft_ms"),
                completion_tokens_source=completion_tokens_source,
                completion_tokens=completion_tokens_u,
                **resp_summary,
            )

        # Log non-streaming request completion summary
        log(
            "INFO",
            "request_completed",
            req_id=req_id,
            status=200,
            elapsed_ms=round(elapsed_ms, 2),
            did_summarize=did_summarize,
            passthrough=is_passthrough,
            prompt_tokens=prompt_tokens_for_log or completion_tokens_u,
            completion_tokens=completion_tokens_u or 0,
            total_tokens=resp_summary.get("total_tokens", 0),
            finish_reason=resp_summary.get("finish_reason"),
        )

        return JSONResponse(data, status_code=200)
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        
        # Estimate TTFT for non-streaming: assume ~30% of total time spent on prompt processing
        if ttft_ms_non_stream is None and elapsed_ms > 0:
            ttft_ms_non_stream = elapsed_ms * 0.3
        
        # Categorize the error type
        error_type = "unknown"
        err_msg = str(e)[:500]
        
        if isinstance(e, httpx.ConnectError):
            error_type = "connection_failed"
            err_msg = f"Connection failed to {url}"
            log_connection_error(
                req_id=req_id,
                error_type=error_type,
                upstream_url=url,
                model=upstream_model,
                elapsed_ms=elapsed_ms,
                err=str(e)[:300],
            )
        elif isinstance(e, httpx.ConnectTimeout):
            error_type = "connection_timeout"
            err_msg = f"Connection timeout to {url}"
            log_connection_error(
                req_id=req_id,
                error_type=error_type,
                upstream_url=url,
                model=upstream_model,
                elapsed_ms=elapsed_ms,
                err=str(e)[:300],
            )
        elif isinstance(e, httpx.TimeoutException):
            error_type = "timeout"
            err_msg = str(e)[:200]
        elif isinstance(e, httpx.HTTPStatusError):
            error_type = "http_status_error"
            status = getattr(e, 'response', None)
            if status:
                err_msg = f"HTTP {status.status_code}: {str(e)[:150]}"
        
        # Log generic request error
        log(
            "ERROR",
            "request_completed_error",
            req_id=req_id,
            status=502,
            elapsed_ms=round(elapsed_ms, 2),
            did_summarize=did_summarize,
            passthrough=is_passthrough,
            error_type=error_type,
            err=err_msg,
        )

        log(
            "ERROR",
            "proxy_exception",
            req_id=req_id,
            elapsed_ms=round(elapsed_ms, 2),
            err=err_msg,
            did_summarize=did_summarize,
            passthrough=is_passthrough,
        )
        
        # Raise a clean exception without httpx traceback
        raise HTTPException(status_code=502, detail={"error": err_msg, "error_type": error_type})
