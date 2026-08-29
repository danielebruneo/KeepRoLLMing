"""Chat completions endpoint handler.

This module provides the /v1/chat/completions endpoint handler with full
streaming support, summarization orchestration, fallback chains, and
OpenAI compatibility layer.
"""

import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Set

import httpx
from fastapi import Response
from fastapi.responses import JSONResponse

from ..logger import log, extract_last_user_text
from .. import logger as _logger
from ..utils.dump import dump_failed_payload
from ..config import DEFAULTS, resolve_route_settings, UPSTREAM_BASE_URL, resolve_route
from ..routing import (
    get_route_settings,
    RoutePlan,
)

from ..token_counter import TokenCounter
from ..types import DEFAULT_REQUEST_TIMEOUT
from ..upstream import http_client, make_request_timeout
from ..processing import _count_tokens_safe
from ..observability import events_execution as _exec

# Import streaming handlers
from .streaming_handlers import process_streaming_request, _strip_last_image_url

# Import from summary package (used by this endpoint)
from ..summary import (
    should_summarise,
    split_messages,
    _pinned_head_count,
)

# Import summarize_incremental explicitly from summary package
from ..summary import (
    summarize_incremental as _rs_summarize_incremental,
)

# O10: emit performance events instead of direct record_metrics() call
from ..observability import events_execution as _exec_perf

# Alias for consistency - this ensures the test's patching works correctly
summarize_incremental = _rs_summarize_incremental

TOK = TokenCounter()


async def _post_upstream(client, url, *, json, headers, request_timeout: float):
    """Post with the route deadline; tolerate minimal legacy test doubles."""
    try:
        return await client.post(
            url, json=json, headers=headers,
            timeout=make_request_timeout(request_timeout),
        )
    except TypeError as exc:
        if "timeout" not in str(exc):
            raise
        return await client.post(url, json=json, headers=headers)


async def _retry_strip_last_image_non_streaming(
    effective_payload: Dict[str, Any],
    err_text: str,
    route: Any,
    req_id: str,
    client: httpx.AsyncClient,
    url: str,
    route_headers: Dict[str, str],
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> Optional[JSONResponse]:
    """Non-streaming version: strip last image on tokenizer error, retry.

    Uses the same config key (strip_last_image_on_error) as the streaming
    version in streaming_handlers.py.

    Returns a JSONResponse on success, None if no retry was attempted or
    all retries failed.
    """
    # Check if feature is enabled in route config
    fc = getattr(route, 'filters', None)
    if not fc or not isinstance(fc, dict):
        return None
    mcfg = fc.get("multimodal_validator", {})
    if not mcfg.get("strip_last_image_on_error", False):
        return None

    # Only retry on tokenizer-like errors
    if "tokenize" not in err_text.lower() and "bitmaps" not in err_text:
        return None

    messages = effective_payload.get("messages", [])
    max_iterations = mcfg.get("strip_last_image_max_retries", 41)
    route_key = _resolve_route_key(route)

    for attempt in range(max_iterations):
        stripped = _strip_last_image_url(messages)
        if not stripped:
            _exec.emit_strip_image_done(req_id, attempt)
            return None

        remaining = sum(
            1 for m in messages
            for p in (m.get("content") if isinstance(m.get("content"), list) else [])
            if isinstance(p, dict) and p.get("type") == "image_url"
        )
        _exec.emit_strip_image_retry(req_id, attempt + 1, remaining)

        try:
            r = await _post_upstream(client, url, json=effective_payload,
                                     headers=route_headers, request_timeout=request_timeout)
        except Exception as e:
            _exec.emit_strip_image_error(req_id, attempt + 1, str(e))
            return None

        if r.status_code < 400:
            body = await r.aread()
            return JSONResponse(
                content=json.loads(body),
                status_code=r.status_code,
                headers=dict(r.headers) if r.headers else None,
            )

        # Still failing
        err2 = (await r.aread()).decode("utf-8", errors="replace")
        _exec.emit_strip_image_failed(req_id, attempt + 1, r.status_code, err2[:200])

    return None


def _resolve_route_key(route: Any) -> str:
    """Extract route key from route object."""
    return route.name if hasattr(route, 'name') else str(route)


# ── Helper functions for process_chat_request ──────────────────────────────


def _parse_request(payload, headers, req_id):
    """Parse and log the incoming request."""
    user_id = headers.get("x-librechat-user-id", "")
    conv_id = headers.get("x-librechat-conversation-id", "")
    client_model = payload.get("model")
    messages = payload.get("messages", [])
    stream = bool(payload.get("stream", False))
    max_tokens_req = payload.get("max_tokens")
    if not isinstance(messages, list):
        raise ValueError("Invalid payload: messages must be a list")
    _exec.emit_http_in(req_id, client_model, stream=stream, max_tokens=max_tokens_req,
                       message_count=len(messages), user_id=user_id, conv_id=conv_id)
    # Emit debug event unconditionally; projector configuration decides visibility.
    # Projector level filtering controls visibility (structured@TRACE captures it).
    from ..logger import snip_json
    from ..auth import redact_sensitive_headers
    _exec.emit_request_received(
        req_id, header=redact_sensitive_headers(headers), body_json=snip_json(payload)
    )
    # Emit conversation events unconditionally; projector configuration decides visibility.
    # Projector level filtering controls visibility (main@BASIC shows essential ones).
    _log_messages_basic_plain(messages, req_id)
    return user_id, conv_id, client_model, messages, stream, max_tokens_req


def _log_messages_basic_plain(messages, req_id):
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    content = c.get("text", "")
                    break
        if role == "assistant" and isinstance(msg.get("tool_calls"), list):
            for tc in msg["tool_calls"]:
                if isinstance(tc, dict) and "function" in tc:
                    func = tc["function"]
                    _exec.emit_tool_call(req_id, tool_calls=[{
                        "id": tc.get("id",""), "index": 0, "type": tc.get("type","function"),
                        "function": {"name": func.get("name"), "arguments": func.get("arguments","")}}])
                else:
                    _exec.emit_tool_call(req_id, tool_calls=[{"id": tc.get("id",""), "index": 0, "_raw": str(tc)}])
            continue
        if role in ("tool", "function"):
            tool_call_id = msg.get("tool_call_id") or msg.get("id")
            result_content = content
            if isinstance(result_content, str):
                try:
                    result_content = json.loads(result_content)
                except Exception:
                    pass
            _exec.emit_tool_result(req_id, tool_call_id=tool_call_id, name=msg.get("name"), content=result_content)
            continue
        _exec.emit_conversation(req_id, role=role, text=content)


def _log_messages_basic(messages, req_id):
    system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    if system_msgs:
        system_text = " ".join(m.get("content","") if isinstance(m.get("content",""),str) else "" for m in system_msgs).strip()
        if system_text:
            _exec.emit_conversation(req_id, role="system", text=system_text)
    last_user = extract_last_user_text(messages)
    if last_user:
        _exec.emit_conversation(req_id, role="user", text=last_user)


def _resolve_route_context(
    client_model, messages, payload, max_tokens_req, req_id, *, route=None, model=None,
):
    """Resolve route and return a typed context dict (transitioning to RouteSettings)."""
    if route is None:
        route, model = resolve_route(client_model, req_id=req_id)
    if route is None:
        _exec.emit_route_not_found(req_id, client_model)
        raise ValueError(f"No route found for model: {client_model}")
    assert route is not None
    rs = get_route_settings(route, model)
    route_upstream_url = rs.upstream_url or UPSTREAM_BASE_URL
    if not route_upstream_url or route_upstream_url is None:
        _exec.emit_missing_upstream(req_id, route.name)
        route_upstream_url = "http://localhost:1234"
    elif isinstance(route_upstream_url, str) and not route_upstream_url.startswith(("http://", "https://")):
        _exec.emit_invalid_upstream(req_id, route.name, route_upstream_url)
        route_upstream_url = f"http://{route_upstream_url}"
    resolved_ctx_len, resolved_max_tokens = resolve_route_settings(route, {}, _get_default_settings())  # type: ignore[arg-type]
    ctx_eff = resolved_ctx_len
    max_out = _clamp_max_out_for_ctx(max_tokens_req, ctx_eff)
    plan = should_summarise(tok=TOK, messages=messages, ctx_eff=ctx_eff, max_out=max_out)
    _, non_system = split_messages(messages)
    pinned_head_n = _pinned_head_count(non_system)
    custom_prompt_type = payload.get("summary_prompt_type")
    custom_prompt_text = payload.get("summary_prompt")
    if custom_prompt_text and isinstance(custom_prompt_text, str) and not custom_prompt_type:
        custom_prompt_type = custom_prompt_text
    route_plan = RoutePlan.compile(
        route=route,
        client_model=client_model,
        model=model,
        settings=rs,
        context_window=resolved_ctx_len,
        default_max_tokens=resolved_max_tokens,
        upstream_url=route_upstream_url,
    )
    _exec.emit_route_resolved(
        req_id, client_model, route_plan.route_name, model, route_plan.upstream_model,
        route_plan.summary_model, route_plan.settings.passthrough_enabled,
        route_plan.context_window, route_plan.default_max_tokens,
        list(route_plan.route_hierarchy[:-1]),
    )
    result = {"route": route, "model": model, "upstream_model": route_plan.upstream_model,
        "summary_model": route_plan.summary_model,
        "is_passthrough": route_plan.settings.passthrough_enabled,
        "is_summary_enabled": route_plan.settings.summary_enabled,
        "transform_reasoning_content": route_plan.settings.transform_reasoning_content,
        "add_empty_content_when_reasoning_only": route_plan.settings.add_empty_content_when_reasoning_only,
        "reasoning_placeholder": route_plan.settings.reasoning_placeholder_content,
        "route_upstream_url": route_plan.upstream_url,
        "route_headers": dict(route_plan.upstream_headers),
        "api_key": route_plan.settings.api_key,
        "ctx_eff": route_plan.context_window,
        "resolved_max_tokens": route_plan.default_max_tokens,
        "request_timeout": route_plan.request_timeout, "url": route_plan.endpoint_url,
        "route_plan": route_plan,
        "plan": plan, "pinned_head_n": pinned_head_n,
        "custom_prompt_type": custom_prompt_type, "custom_prompt_text": custom_prompt_text}
    return result


async def _handle_summarization(messages, payload, user_id, conv_id, ctx, req_id):
    did_summarize = False
    summary_tokens = 0
    repacked_messages = messages
    skip_summary_for_tools = _is_tool_orchestration_payload(payload, messages)
    _summarize_decision(is_passthrough=ctx["is_passthrough"],
        skip_summary_for_tools=skip_summary_for_tools, plan=ctx["plan"],
        is_summary_enabled=ctx["is_summary_enabled"], req_id=req_id)
    if (not ctx["is_passthrough"]) and (not skip_summary_for_tools) and ctx["is_summary_enabled"] and ctx["plan"].should:
        # V2: delegate to SummarizationFilter (wired May 2026)
        from ..filters.summarization import SummarizationFilter
        from ..orchestrator.filter import FilterExecutionContext
        sf = SummarizationFilter()
        sfctx = FilterExecutionContext(
            req_id=req_id,
            upstream_model=ctx["summary_model"],
            upstream_payload=dict(payload, user_id=user_id, conv_id=conv_id),
        )
        sfctx.metadata.update({
            "route": ctx["route"], "plan": ctx["plan"],
            "custom_prompt_type": ctx["custom_prompt_type"],
            "custom_prompt_text": ctx["custom_prompt_text"],
            "pinned_head_n": ctx["pinned_head_n"], "ctx_eff": ctx["ctx_eff"],
            "is_summary_enabled": ctx["is_summary_enabled"],
        })
        class _Req:
            messages: list = []
            model: str = ""
            stream: bool = False
            metadata: dict = {}
        req = _Req()
        req.messages = list(messages)
        await sf.process_request(req, sfctx)
        repacked_messages = req.messages
        did_summarize = sfctx.metadata.get("did_summarize", False)
        summary_tokens = sfctx.metadata.get("summary_tokens", 0)
    return repacked_messages, did_summarize, summary_tokens


def _build_upstream_payload(
    payload, upstream_model, repacked_messages, route_plan: RoutePlan,
    max_tokens_req, ctx_eff, req_id,
):
    upstream_payload = dict(payload)
    upstream_payload["_original_model"] = upstream_payload.get("model", payload.get("model",""))
    upstream_payload["model"] = upstream_model
    upstream_payload["messages"] = repacked_messages
    # OpenAI-compatible providers commonly send final token usage for SSE only
    # when this option is explicitly requested.  Preserve an explicit client
    # choice, but request it by default so runtime accounting is authoritative.
    if upstream_payload.get("stream"):
        stream_options = upstream_payload.get("stream_options")
        if isinstance(stream_options, dict):
            stream_options = dict(stream_options)
        else:
            stream_options = {}
        stream_options.setdefault("include_usage", True)
        upstream_payload["stream_options"] = stream_options
    overrides = route_plan.build_overrides()
    if overrides:
        from ..overrides import apply_overrides
        applied = apply_overrides(upstream_payload, overrides)
        if applied:
            for key, old_val, new_val in applied:
                _exec.emit_override(req_id, param=key, old_value=old_val, new_value=new_val)
    prompt_tokens_for_log = _count_tokens_safe(repacked_messages)
    _clamp_max_tokens_upstream(payload=upstream_payload, max_tokens_req=max_tokens_req,
                               ctx_eff=ctx_eff, prompt_tokens_for_log=prompt_tokens_for_log)
    return upstream_payload


def _log_upstream_request(upstream_payload, ctx, did_summarize, req_id):
    req_summary = summarize_request_payload(upstream_payload)
    _exec.emit_repacked(req_id, did_summarize, ctx["is_passthrough"],
                        f'{ctx["route_upstream_url"]}/v1/chat/completions',
                        _count_tokens_safe(upstream_payload.get("messages",[])),
                        adjusted_max_tokens=upstream_payload.get("max_tokens"))


def _setup_fallback(route_plan: RoutePlan, req_id: str):
    """Materialize retry state from the compiled plan, not raw route config."""
    fallback_attempts = []
    visited_models = {route_plan.settings.upstream_model}
    # Passthrough routes forward directly — no fallback chain
    if route_plan.settings.passthrough_enabled:
        return fallback_attempts, visited_models
    if route_plan.settings.fallback_chain:
        _exec.emit_fallback_chain(
            req_id, route_plan.settings.fallback_chain, route_plan.settings.upstream_model
        )
        fallback_attempts = list(route_plan.fallback_attempts)
    return fallback_attempts, visited_models


def _build_pipeline_if_configured(route):
    """Build V2 Pipeline from canonical route filters configuration."""
    if not (route and hasattr(route, 'filters') and route.filters):
        return None
    from ..orchestrator.pipeline import Pipeline
    return Pipeline.from_route_config(
        route.filters,
        api_key=getattr(route, 'api_key', None),
    )


def _MockResponse_for_pipeline(r=None, *, content=None, model=None, usage=None, finish_reason=None, tool_calls=None):
    """Build a filter-compatible StreamingResponse from an httpx Response or kwargs."""
    from ..orchestrator.filter import StreamingResponse

    _content = content or ""
    _model = model or "unknown"
    _usage = usage
    _finish_reason = finish_reason
    _tool_calls = tool_calls or []
    _reasoning_content = ""

    if r is not None:
        _model = model or "unknown"
        _content = r.content.decode("utf-8", errors="replace")
        try:
            resp_json = r.json()
            choices = resp_json.get("choices", [])
            if choices and isinstance(choices[0], dict):
                msg_data = choices[0].get("message", {})
                _content = msg_data.get("content", "") or ""
                _tool_calls = msg_data.get("tool_calls", [])
                _reasoning_content = msg_data.get("reasoning_content", "") or ""
            _usage = resp_json.get("usage")
            _finish_reason = choices[0].get("finish_reason") if choices else None
        except Exception:
            pass

    return StreamingResponse(
        content=_content,
        model=_model,
        finish_reason=_finish_reason,
        tool_calls=_tool_calls,
        usage=_usage,
        reasoning_content=_reasoning_content,
    )


async def process_chat_request(payload, headers, req_id) -> Response | AsyncIterator[bytes]:
    """Process a chat completion request with full orchestration."""
    client_model = payload.get("model")
    if not isinstance(client_model, str) or not client_model:
        return JSONResponse(
            {"error": {"message": "Missing or invalid model"}}, status_code=400
        )

    # Resolve and authorize before request transcript logging, filtering, raw
    # capture, or any upstream work. ``api_key`` remains upstream-only;
    # ``api_keys`` are client credentials.
    route, model = resolve_route(client_model, req_id=req_id)
    from ..auth import bearer_token, authentication_error_response, is_authorized
    if route is None or not is_authorized(headers, route.api_keys or []):
        from ..app import get_event_dispatcher
        from ..observability import events_request as _request_events
        _request_events.emit_auth_rejected(
            req_id,
            route=route.name if route is not None else "unresolved",
            endpoint="/v1/chat/completions",
            credential_present=bearer_token(headers) is not None,
            dispatcher=get_event_dispatcher(),
        )
        return authentication_error_response()

    _exec.emit_request_start(req_id, stream=payload.get("stream", False))
    t_start = time.perf_counter()
    try:
        user_id, conv_id, client_model, messages, stream, max_tokens_req = _parse_request(payload, headers, req_id)
    except ValueError as e:
        return JSONResponse({"error": {"message": str(e)}}, status_code=400)
    ctx = _resolve_route_context(
        client_model, messages, payload, max_tokens_req, req_id,
        route=route, model=model,
    )

    # ── Summarization: V2 Pipeline (non-streaming handled in process_non_streaming_request) ──
    # For streaming: request processing happens in streaming handler
    # For non-streaming: request processing happens in process_non_streaming_request
    repacked_messages, did_summarize, summary_tokens = await _handle_summarization(
        messages=messages, payload=payload, user_id=user_id, conv_id=conv_id, ctx=ctx, req_id=req_id)
    upstream_payload = _build_upstream_payload(payload, ctx["upstream_model"], repacked_messages,
                                                ctx["route_plan"], max_tokens_req, ctx["ctx_eff"], req_id)

    # O12: Emit raw request capture event (post-route-resolution, pre-filter-chain)
    from ..app import get_event_dispatcher
    _exec.emit_request_capture(
        req_id=req_id,
        raw_body=upstream_payload,
        client_model=client_model,
        resolved_route=ctx["route"].name,
        upstream_model=ctx["upstream_model"],
        upstream_url=ctx["url"],
        route_hierarchy=getattr(ctx["route"], '_route_hierarchy', []),
        dispatcher=get_event_dispatcher(),
    )

    _log_upstream_request(upstream_payload, ctx, did_summarize, req_id)
    client = await http_client(ctx["request_timeout"])
    fallback_attempts, visited_models = _setup_fallback(ctx["route_plan"], req_id)
    _exec.emit_request_route(req_id, stream, ctx["route"].name,
                             filters=list(ctx["route_plan"].enabled_filters))
    route_headers = ctx["route_plan"].build_upstream_headers()
    pipeline = ctx["route_plan"].build_pipeline()
    # Phase P5: wire dispatcher through streaming handler call chain
    from ..app import get_event_dispatcher
    dispatcher = get_event_dispatcher()
    if stream:
        return process_streaming_request(
            url=ctx["url"], client=client, payload=upstream_payload,
            route_headers=route_headers, route=ctx["route"], req_id=req_id,
            request_timeout=ctx["request_timeout"], fallback_attempts=fallback_attempts,
            visited_models=visited_models, upstream_model=ctx["upstream_model"],
            is_passthrough=ctx["is_passthrough"],
            transform_reasoning_content=ctx["transform_reasoning_content"],
            add_empty_content_when_reasoning_only=ctx["add_empty_content_when_reasoning_only"],
            reasoning_placeholder=ctx["reasoning_placeholder"], t_start=t_start,
            pipeline=pipeline,
            enabled_filters=ctx["route_plan"].enabled_filters,
            record_metrics_func=lambda m: _record_final_metrics(m, t_start=t_start, did_summarize=did_summarize, route_name=ctx["route"].name, route=ctx["route"]),
            dispatcher=dispatcher)
    else:
        return await process_non_streaming_request(
            url=ctx["url"], client=client, payload=upstream_payload,
            route_headers=route_headers, req_id=req_id,
            upstream_model=ctx["upstream_model"], fallback_attempts=fallback_attempts,
            visited_models=visited_models, t_start=t_start,
            did_summarize=did_summarize, route_name=ctx["route"].name, route=ctx["route"],
            pipeline=pipeline, enabled_filters=ctx["route_plan"].enabled_filters,
            request_timeout=ctx["request_timeout"])


async def process_non_streaming_request(
    url: str,
    client: httpx.AsyncClient,
    payload: Dict[str, Any],
    route_headers: Dict[str, str],
    req_id: str,
    upstream_model: str,
    fallback_attempts: List[Dict[str, str]],
    visited_models: Set[str],
    t_start: float,
    did_summarize: bool = False,
    route_name: str = "",
    route=None,  # Route object for filters integration
    pipeline=None,
    enabled_filters: Sequence[str] | None = None,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> Response:
    """Process a non-streaming chat completion request.
    
    Args:
        url: Upstream API URL
        client: HTTPX async client
        payload: Request payload
        route_headers: Route-specific headers
        req_id: Request ID
        upstream_model: Primary model being used
        fallback_attempts: List of fallback models to try on failure
        visited_models: Set of models already attempted
        t_start: Start timestamp for timing calculations
        did_summarize: Whether summarization was performed
        route_name: Name of the route that handled the request
        
    Returns:
        FastAPI Response object
    """
    
     # ABSOLUTE_DEBUG: this log MUST appear if code is reached
    has_filters = bool(route and hasattr(route, 'filters') and route.filters)
    filters_list = list(enabled_filters or [])

    _exec.emit_route_resolved(req_id, client_model="", resolved_route=route_name,
                              model="", upstream_model="", summary_model="",
                              passthrough_enabled=False, ctx_len=0,
                              max_tokens_default=0, parent_routes=[],
                              has_filters=has_filters,
                              filters=filters_list)
    
    # Prepare upstream payload for retry logic (filter chain can modify messages)
    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model

    # ── Process request through V2 Pipeline ──────────────────────────
    effective_payload = dict(payload)
    if pipeline is None:
        pipeline = _build_pipeline_if_configured(route)
    if pipeline:
        effective_payload = await pipeline.process_request(
            effective_payload, req_id, upstream_model,
            route_name=route_name,
            upstream_url=getattr(route, 'upstream_url', None) or UPSTREAM_BASE_URL,
        )

    # Reset elapsed timer after filter chain (metrics measure upstream time, not overhead)
    t_start = time.perf_counter()

    try:
        r = await _post_upstream(client, url, json=effective_payload,
                                 headers=route_headers, request_timeout=request_timeout)
        
        if r.status_code >= 400:
            err_bytes = await r.aread()
            err_text = err_bytes.decode("utf-8", errors="replace")

            # O11: emit full error body (not truncated) so BodyCaptureConsumer
            # can capture high-fidelity data. Logger/formatters truncate their
            # projection if needed (P4: Event Data and Log Representation are separate).
            _exec.emit_upstream_error(req_id, r.status_code, url, route_name,
                                      upstream_model, err_text,
                                      request_payload=effective_payload)

            # Dump full payload for debugging multimodal/tokenizer errors
            # PRESERVED for parity verification between __dumps__/ and __body_captures__/
            await dump_failed_payload(
                req_id, effective_payload, r.status_code, err_text,
                upstream_model=upstream_model,
                upstream_url=url,
                route=route_name,
            )

            # ── Retry by stripping last image on tokenizer error ──
            retry_response = await _retry_strip_last_image_non_streaming(
                effective_payload, err_text,
                route, req_id, client, url, route_headers, request_timeout,
            )
            if retry_response is not None:
                return retry_response

            # Try fallback models
            for route_opt, fallback_model in fallback_attempts:
                if fallback_model not in visited_models:
                    _exec.emit_fallback(req_id, upstream_model, fallback_model)
                    payload["model"] = fallback_model
                    visited_models.add(fallback_model)
                    
                    try:
                        r = await _post_upstream(client, url, json=payload,
                                                 headers=route_headers, request_timeout=request_timeout)
                        if r.status_code < 400:
                            break
                    except Exception:
                        visited_models.add(fallback_model)
                        continue
            
            # If still failed, return error response
            if r.status_code >= 400:
                return JSONResponse({
                    "error": {
                        "message": "Upstream error",
                        "details": err_text[:2000],
                        "route": route_name,
                        "upstream_model": upstream_model,
                        "url": url,
                        "status": r.status_code,
                    }
                }, status_code=r.status_code)
        # ── Apply V2 Pipeline to response ───────────────────────────
        processed_response = None
        if pipeline:
            async def _retry_upstream(p):
                p["model"] = upstream_model
                r = await _post_upstream(client, url, json=p,
                                         headers=route_headers, request_timeout=request_timeout)
                try:
                    return r.json()
                except Exception:
                    return None

            mock_response = _MockResponse_for_pipeline(r, model=upstream_model)
            try:
                processed_response = await pipeline.process_response(
                    mock_response, payload, req_id, upstream_model,
                    route_name=getattr(route, 'name', ''),
                    upstream_url=getattr(route, 'upstream_url', None) or UPSTREAM_BASE_URL,
                    is_streaming=False,
                    upstream_caller=_retry_upstream,
                )
            except Exception as e:
                _exec.emit_pipeline_error(req_id, str(e))

        # Extract usage data from response for metrics
        response_json = r.json()
        usage = response_json.get("usage") or {}

        # Create ExecutionUsage for non-streaming mode (Phase 1 internal accounting)
        from ..streaming.accounting import ExecutionUsage
        _execution_usage = ExecutionUsage.empty()
        _execution_usage.add_attempt(0, usage)
        _execution_usage.finalize()

        completion_tokens = usage.get("completion_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        total_tokens = usage.get("total_tokens", (completion_tokens or 0) + (prompt_tokens or 0))

        # Extract cached_tokens from prompt_tokens_details
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens")

        # If filter chain modified the response (e.g. nudge retry), estimate tokens from processed content
        # because the original usage only reflects the first (lazy) response, not the accumulated result
        if processed_response and hasattr(processed_response, 'content') and processed_response.content:
            from ..token_counter import TokenCounter
            tc = TokenCounter()
            processed_content = processed_response.content or ""
            completion_tokens = tc.count_text(processed_content)
            # Also count tool_calls JSON arguments, if any
            if hasattr(processed_response, 'tool_calls') and processed_response.tool_calls:
                for tc_call in processed_response.tool_calls:
                    fn = tc_call.get("function", {})
                    args = fn.get("arguments", "{}")
                    completion_tokens += tc.count_text(args)
            total_tokens = (prompt_tokens or 0) + completion_tokens
            # Reset cached_tokens since response was modified
            cached_tokens = None
        
        # Calculate elapsed time and record metrics
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        
        # Use ExecutionUsage if available (Phase 1 internal accounting)
        if _execution_usage is not None:
            _record_final_metrics({
                "model": response_json.get("model", upstream_model),
                "req_id": req_id,
                "stream": False,
                "ttft_ms": None,
                "elapsed_ms": elapsed_ms,
                "completion_tokens": _execution_usage.final_completion_tokens,
                "prompt_tokens": _execution_usage.final_prompt_tokens,
                "total_tokens": (
                    _execution_usage.final_prompt_tokens + _execution_usage.final_completion_tokens
                    if _execution_usage.final_prompt_tokens is not None
                    and _execution_usage.final_completion_tokens is not None
                    else None
                ),
                "finish_reason": response_json.get("choices", [{}])[0].get("finish_reason"),
                "passthrough": False,
                "completion_tokens_source": "usage",
                "upstream_attempts": _execution_usage.upstream_attempts,
                "usage_reported_attempts": _execution_usage.usage_reported_attempts,
                "usage_complete": _execution_usage.usage_complete,
                "recovery_count": _execution_usage.recovery_count,
                "upstream_prompt_tokens": _execution_usage.upstream_prompt_tokens,
                "upstream_completion_tokens": _execution_usage.upstream_completion_tokens,
                "upstream_total_tokens": _execution_usage.upstream_total_tokens,
                "cached_prompt_tokens": cached_tokens,
            }, t_start=t_start, did_summarize=did_summarize, route_name=route_name, route=route)
        else:
            # For non-streaming requests, set ttft_ms=0 so completion_tps uses full elapsed time
            # This gives us: completion_tps = completion_tokens / (elapsed / 1000.0)
            _record_final_metrics({
                "model": response_json.get("model", upstream_model),
                "req_id": req_id,
                "stream": False,
                "ttft_ms": None,
                "elapsed_ms": elapsed_ms,
                "completion_tokens": completion_tokens,
                "prompt_tokens": prompt_tokens,
                "total_tokens": total_tokens,
                "finish_reason": response_json.get("choices", [{}])[0].get("finish_reason"),
                "passthrough": False,
                "completion_tokens_source": "usage" if usage else "missing",
                "cached_prompt_tokens": cached_tokens,
            }, t_start=t_start, did_summarize=did_summarize, route_name=route_name, route=route)
        # Emit the assistant response for all configured projectors.
        try:
            # Use processed_response content if available (from filter chain processing)
            if processed_response and hasattr(processed_response, "content"):
                assistant_text = processed_response.content or ""
            else:
                resp_body = r.json()
                choices = resp_body.get("choices", [])
                assistant_text = ""
                if choices and isinstance(choices[0], dict):
                    msg_data = choices[0].get("message", {})
                    assistant_text = msg_data.get("content", "") or ""

            # Capture tool_calls names for logging (non-streaming)
            tc_names = []
            if processed_response and hasattr(processed_response, 'tool_calls') and processed_response.tool_calls:
                for tc_call in processed_response.tool_calls:
                    fn = tc_call.get("function", {})
                    name = fn.get("name", "")
                    if name:
                        tc_names.append(name)
            elif processed_response is None:
                resp_body = r.json()
                choices = resp_body.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg_data = choices[0].get("message", {})
                    raw_tcs = msg_data.get("tool_calls", [])
                    for tc_call in raw_tcs:
                        fn = tc_call.get("function", {})
                        name = fn.get("name", "")
                        if name:
                            tc_names.append(name)

            # Capture reasoning content
            reasoning_text = ""
            if processed_response and hasattr(processed_response, 'reasoning_content'):
                reasoning_text = processed_response.reasoning_content or ""
            elif processed_response is None:
                resp_body = r.json()
                choices = resp_body.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg_data = choices[0].get("message", {})
                    reasoning_text = msg_data.get("reasoning_content", "") or ""

            _exec.emit_assistant(req_id, assistant_text if assistant_text else "",
                                 len(assistant_text), tool_calls=tc_names if tc_names else None,
                                 reasoning_content=reasoning_text,
                                 reasoning_length=len(reasoning_text))
        except Exception:
            pass

        # Log cache metrics if available
        if cached_tokens is not None and prompt_tokens is not None and prompt_tokens > 0:
            cache_pct = round((cached_tokens / prompt_tokens) * 100, 1)
            _exec.emit_cache_metrics(
                req_id=req_id,
                cached_tokens=cached_tokens,
                prompt_tokens=prompt_tokens,
                cache_pct=cache_pct,
            )

        # Return full JSON response, updating content if processed_response is available (from filter chain processing)
        try:
            resp_json = r.json()
            # If filter chain processed the response (e.g., nudge retry), use its content
            if processed_response and hasattr(processed_response, 'content'):
                # Update the content in the original JSON response structure
                choices = resp_json.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg_data = choices[0].get("message", {})
                    msg_data["content"] = processed_response.content or ""
                    # Propagate tool_calls if filter chain returned them
                    if hasattr(processed_response, 'tool_calls') and processed_response.tool_calls:
                        msg_data["tool_calls"] = processed_response.tool_calls
                        choices[0]["finish_reason"] = "tool_calls"
                    # Otherwise propagate the filter's (normalized) finish_reason so a
                    # stale "length" from the original lazy response doesn't leak and
                    # trigger a spurious client-side truncation notice.
                    elif getattr(processed_response, 'finish_reason', None):
                        choices[0]["finish_reason"] = processed_response.finish_reason
                    # Propagate reasoning_content from filter chain (e.g. TLS/nudge retries)
                    if (hasattr(processed_response, 'reasoning_content')
                            and processed_response.reasoning_content):
                        msg_data["reasoning_content"] = processed_response.reasoning_content

                _exec.emit_http_out(req_id, r.status_code)
                return JSONResponse(content=resp_json, status_code=r.status_code)

            # No filter processing - return original response
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception:
            # Fallback: return raw response if JSON parsing fails
            return Response(content=r.content, status_code=r.status_code, media_type="application/json")
    except httpx.TimeoutException:
        _exec.emit_timeout(req_id)
        # Record metrics even on timeout
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        _record_final_metrics({
            "model": upstream_model,
            "req_id": req_id,
            "stream": False,
            "ttft_ms": None,  # Non-streaming: TTFT not meaningful
            "elapsed_ms": elapsed_ms,
            "completion_tokens": None,
            "prompt_tokens": None,
            "total_tokens": None,
            "finish_reason": "timeout",
            "passthrough": False,
            "completion_tokens_source": "missing",
        }, t_start=t_start, did_summarize=did_summarize, route_name=route_name, route=route)
        return JSONResponse({"error": {"message": "Request timeout"}}, status_code=504)
    except Exception as e:
        import traceback
        _exec.emit_failed(req_id, str(e), url, route_name, upstream_model,
                          traceback.format_exc())
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        _record_final_metrics({
            "model": upstream_model, "req_id": req_id, "stream": False,
            "ttft_ms": None, "elapsed_ms": elapsed_ms,
            "completion_tokens": None, "prompt_tokens": None, "total_tokens": None,
            "finish_reason": str(e), "passthrough": False,
            "completion_tokens_source": "missing",
        }, t_start=t_start, did_summarize=did_summarize, route_name=route_name, route=route)
        return JSONResponse({
            "error": {
                "message": str(e),
                "route": route_name,
                "upstream_model": upstream_model,
                "url": url,
                "hint": "Check UPSTREAM_BASE_URL env var or route config upstream_url"
            }
        }, status_code=500)


def _get_default_settings():
    """Get default settings object."""
    return DEFAULTS


def _clamp_max_out_for_ctx(max_tokens_req: Optional[int], ctx_eff: int) -> int:
    """Clamp max_tokens to fit within context window with safety margin."""
    from ..config import SAFETY_MARGIN_TOK
    max_out = 64
    if max_tokens_req is not None and max_tokens_req > 0:
        max_out = min(max_tokens_req, ctx_eff - SAFETY_MARGIN_TOK)
    return max(max_out, 64)


def _is_tool_orchestration_payload(payload: Dict[str, Any], messages: List[Dict]) -> bool:
    """Check if payload is a tool orchestration request."""
    if len(messages) < 2:
        return False
    
    last_msg = messages[-1]
    second_last_msg = messages[-2]
    
    has_tool_calls = "tool_calls" in last_msg.get("function_call", {}) or "tool_calls" in str(last_msg)
    
    return (
        second_last_msg.get("role") == "assistant" and
        last_msg.get("role") == "tool" and
        (has_tool_calls or "tool_call_id" in last_msg)
    )


def _summarize_decision(
    is_passthrough: bool,
    skip_summary_for_tools: bool,
    plan: Any,
    is_summary_enabled: bool,
    req_id: str
) -> None:
    """Log summary bypass decision."""
    if is_passthrough:
        _exec.emit_summary_bypassed(
            req_id=req_id, reason="passthrough_model",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif skip_summary_for_tools:
        _exec.emit_summary_bypassed(
            req_id=req_id, reason="memory_payload",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif not plan.should:
        _exec.emit_summary_bypassed(
            req_id=req_id, reason=plan.reason,
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif not is_summary_enabled:
        _exec.emit_summary_bypassed(
            req_id=req_id, reason="summary_disabled_in_config",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )


def _clamp_max_tokens_upstream(
    payload: Dict,
    max_tokens_req: Optional[int],
    ctx_eff: int,
    prompt_tokens_for_log: Optional[int]
) -> None:
    """Clamp and set max_tokens for upstream request."""
    from ..config import DEFAULT_MAX_COMPLETION_TOKENS, SAFETY_MARGIN_TOK
    
    if DEFAULT_MAX_COMPLETION_TOKENS is not None:
        max_tokens_upstream = max(64, int(ctx_eff) - int(prompt_tokens_for_log or 0) - int(SAFETY_MARGIN_TOK))
        requested_out = int(max_tokens_req) if isinstance(max_tokens_req, int) and max_tokens_req > 0 else DEFAULT_MAX_COMPLETION_TOKENS
        adjusted_out = min(requested_out, max_tokens_upstream)
        payload["max_tokens"] = adjusted_out
        
        if adjusted_out < (requested_out if isinstance(max_tokens_req, int) and max_tokens_req > 0 else 0):
            _exec.emit_max_tokens_clamped(
                req_id="", requested=requested_out, adjusted=adjusted_out,
                ctx_len=ctx_eff, prompt_tokens=prompt_tokens_for_log,
                safety_margin=SAFETY_MARGIN_TOK,
            )


def summarize_request_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize request payload for logging."""
    
    return {
        "messages_count": len(payload.get("messages", [])),
        "max_tokens": payload.get("max_tokens"),
        "temperature": payload.get("temperature"),
    }


def _record_final_metrics(metrics: Dict, t_start: float, did_summarize: bool, route_name: str, route=None) -> None:
    """Record final metrics after request completion.

    O10: Emits execution.performance.request_complete event instead of
    calling record_request_performance() directly. PerformanceConsumer
    subscribes to the event and produces identical output.
    """
    elapsed_ms = (time.perf_counter() - t_start) * 1000.0

    # Ensure metrics dict has all required keys with defaults
    safe_metrics = {
        "model": metrics.get("model", "unknown"),
        "req_id": metrics.get("req_id", ""),
        "stream": metrics.get("stream", False),
        "ttft_ms": metrics.get("ttft_ms"),
        "completion_tokens": metrics.get("completion_tokens"),
        "prompt_tokens": metrics.get("prompt_tokens"),
        "total_tokens": metrics.get("total_tokens"),
        "finish_reason": metrics.get("finish_reason"),
        "passthrough": metrics.get("passthrough", False),
        "completion_tokens_source": metrics.get("completion_tokens_source", "missing"),
        "upstream_prompt_tokens": metrics.get("upstream_prompt_tokens"),
        "upstream_completion_tokens": metrics.get("upstream_completion_tokens"),
        "upstream_total_tokens": metrics.get("upstream_total_tokens"),
        "cached_prompt_tokens": metrics.get("cached_prompt_tokens"),
    }

    route_hierarchy = getattr(route, '_route_hierarchy', [route_name]) if route else [route_name]

    # Extract ExecutionUsage fields (Phase 12)
    upstream_attempts = metrics.get("upstream_attempts", 0)
    usage_reported_attempts = metrics.get("usage_reported_attempts", 0)
    recovery_count = metrics.get("recovery_count", 0)
    retry_amplification_ratio = metrics.get("retry_amplification_ratio", 0.0)
    usage_complete = metrics.get("usage_complete", False)

    # O10: Get global dispatcher and emit event
    from ..app import get_event_dispatcher
    dispatcher = get_event_dispatcher()

    _exec_perf.emit_performance_request_complete(
        req_id=safe_metrics["req_id"],
        model=safe_metrics["model"],
        route_name=route_name,
        route_hierarchy=route_hierarchy,
        stream=safe_metrics["stream"],
        elapsed_ms=elapsed_ms,
        ttft_ms=safe_metrics["ttft_ms"],
        completion_tokens=safe_metrics["completion_tokens"],
        prompt_tokens=safe_metrics["prompt_tokens"],
        total_tokens=safe_metrics["total_tokens"],
        finish_reason=safe_metrics["finish_reason"],
        did_summarize=did_summarize,
        passthrough=safe_metrics["passthrough"],
        completion_tokens_source=safe_metrics["completion_tokens_source"],
        upstream_attempts=upstream_attempts,
        usage_reported_attempts=usage_reported_attempts,
        recovery_count=recovery_count,
        retry_amplification_ratio=retry_amplification_ratio,
        usage_complete=usage_complete,
        upstream_prompt_tokens=safe_metrics["upstream_prompt_tokens"],
        upstream_completion_tokens=safe_metrics["upstream_completion_tokens"],
        upstream_total_tokens=safe_metrics["upstream_total_tokens"],
        cached_prompt_tokens=safe_metrics["cached_prompt_tokens"],
        dispatcher=dispatcher,
    )

    # Emit the human-facing counterpart of the persisted performance record.
    # It deliberately uses the same canonical calculation as
    # PerformanceConsumer, so PLAIN observability and the dashboard cannot
    # disagree about per-request TPS/latency values.
    _exec.emit_derived_performance_metrics(
        req_id=safe_metrics["req_id"],
        elapsed_ms=elapsed_ms,
        completion_tokens=safe_metrics["completion_tokens"],
        ttft_ms=safe_metrics["ttft_ms"],
        prompt_tokens=safe_metrics["prompt_tokens"],
        total_tokens=safe_metrics["total_tokens"],
        cached_prompt_tokens=safe_metrics["cached_prompt_tokens"],
        model=safe_metrics["model"],
        route_name=route_name,
        completion_tokens_source=safe_metrics["completion_tokens_source"],
        dispatcher=dispatcher,
    )
