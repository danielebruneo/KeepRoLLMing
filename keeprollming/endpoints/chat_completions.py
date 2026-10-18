"""Chat completions endpoint handler.

This module provides the /v1/chat/completions endpoint handler with full
streaming support, summarization orchestration, fallback chains, and
OpenAI compatibility layer.
"""

import asyncio
import inspect
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Set

import httpx
from fastapi import Response
from fastapi.responses import JSONResponse

from ..config import (
    DEFAULTS,
    SAFETY_MARGIN_TOK,
    UPSTREAM_BASE_URL,
    USER_ROUTES,
    get_route_settings,
    resolve_execution_route,
    resolve_route,
    resolve_route_settings,
)
from ..logger import extract_last_user_text
from ..observability import events_execution as _exec

# O10: emit performance events instead of direct record_metrics() call
from ..observability import events_execution as _exec_perf
from ..processing import _count_tokens_safe
from ..routing import (
    NoEligibleBalanceTarget,
    RoutePlan,
)

# Import from summary package (used by this endpoint)
from ..summary import (
    _pinned_head_count,
    should_summarise,
    split_messages,
)

# Import summarize_incremental explicitly from summary package
from ..summary import (
    summarize_incremental as _rs_summarize_incremental,
)
from ..token_counter import TokenCounter
from ..types import DEFAULT_REQUEST_TIMEOUT
from ..upstream import http_client, make_request_timeout
from ..utils.dump import dump_failed_payload

# Import streaming handlers
from .streaming_handlers import (
    _is_terminal_client_http_error,
    _strip_last_image_url,
    process_streaming_request,
)

# Alias for consistency - this ensures the test's patching works correctly
summarize_incremental = _rs_summarize_incremental

TOK = TokenCounter()


async def _release_admission_after_stream(stream: AsyncIterator[bytes], lease: Any) -> AsyncIterator[bytes]:
    """Keep an admission lease until the client stream is terminal or closed."""
    try:
        async for chunk in stream:
            yield chunk
    finally:
        await lease.release()


async def _post_upstream(client, url, *, json, headers, request_timeout: float):
    """Post with the route deadline; tolerate minimal legacy test doubles."""
    try:
        return await client.post(
            url,
            json=json,
            headers=headers,
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
    fc = getattr(route, "filters", None)
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
    _resolve_route_key(route)

    for attempt in range(max_iterations):
        stripped = _strip_last_image_url(messages)
        if not stripped:
            _exec.emit_strip_image_done(req_id, attempt)
            return None

        remaining = sum(
            1
            for m in messages
            for p in (m.get("content") if isinstance(m.get("content"), list) else [])
            if isinstance(p, dict) and p.get("type") == "image_url"
        )
        _exec.emit_strip_image_retry(req_id, attempt + 1, remaining)

        try:
            r = await _post_upstream(
                client,
                url,
                json=effective_payload,
                headers=route_headers,
                request_timeout=request_timeout,
            )
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
    return route.name if hasattr(route, "name") else str(route)


def _terminal_upstream_error_response(status_code: int, error_body: str) -> JSONResponse:
    """Propagate a terminal OpenAI-compatible upstream error unchanged."""
    try:
        parsed = json.loads(error_body)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        return JSONResponse(content=parsed, status_code=status_code)
    return JSONResponse(
        {
            "error": {
                "message": error_body or f"Upstream returned HTTP {status_code}",
                "type": "upstream_error",
                "code": status_code,
            }
        },
        status_code=status_code,
    )


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
    _exec.emit_http_in(
        req_id,
        client_model,
        stream=stream,
        max_tokens=max_tokens_req,
        message_count=len(messages),
        user_id=user_id,
        conv_id=conv_id,
    )
    # Emit debug event unconditionally; projector configuration decides visibility.
    # Projector level filtering controls visibility (structured@TRACE captures it).
    from ..auth import redact_sensitive_headers
    from ..logger import snip_json

    _exec.emit_request_received(req_id, header=redact_sensitive_headers(headers), body_json=snip_json(payload))
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
                    _exec.emit_tool_call(
                        req_id,
                        tool_calls=[
                            {
                                "id": tc.get("id", ""),
                                "index": 0,
                                "type": tc.get("type", "function"),
                                "function": {
                                    "name": func.get("name"),
                                    "arguments": func.get("arguments", ""),
                                },
                            }
                        ],
                    )
                else:
                    _exec.emit_tool_call(req_id, tool_calls=[{"id": tc.get("id", ""), "index": 0, "_raw": str(tc)}])
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
        system_text = " ".join(
            m.get("content", "") if isinstance(m.get("content", ""), str) else "" for m in system_msgs
        ).strip()
        if system_text:
            _exec.emit_conversation(req_id, role="system", text=system_text)
    last_user = extract_last_user_text(messages)
    if last_user:
        _exec.emit_conversation(req_id, role="user", text=last_user)


def _resolve_route_context(
    client_model,
    messages,
    payload,
    max_tokens_req,
    req_id,
    *,
    route=None,
    model=None,
    requested_route_name=None,
    reroute_chain=None,
    attempt_routes=None,
    preserved_filters=None,
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
        routes_by_name={candidate.name: candidate for candidate in USER_ROUTES},
        defaults=DEFAULTS,
        requested_route_name=requested_route_name,
        reroute_chain=reroute_chain,
        attempt_routes=attempt_routes,
        preserved_filters=preserved_filters,
    )
    _exec.emit_route_resolved(
        req_id,
        client_model,
        route_plan.route_name,
        model,
        route_plan.upstream_model,
        route_plan.summary_model,
        route_plan.settings.passthrough_enabled,
        route_plan.context_window,
        route_plan.default_max_tokens,
        list(route_plan.route_hierarchy[:-1]),
        requested_route=route_plan.requested_route_name,
        reroute_chain=list(route_plan.reroute_chain),
    )
    result = {
        "route": route,
        "model": model,
        "upstream_model": route_plan.upstream_model,
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
        "request_timeout": route_plan.request_timeout,
        "url": route_plan.endpoint_url,
        "route_plan": route_plan,
        "plan": plan,
        "pinned_head_n": pinned_head_n,
        "custom_prompt_type": custom_prompt_type,
        "custom_prompt_text": custom_prompt_text,
    }
    return result


async def _handle_summarization(messages, payload, user_id, conv_id, ctx, req_id):
    did_summarize = False
    summary_tokens = 0
    repacked_messages = messages
    skip_summary_for_tools = _is_tool_orchestration_payload(payload, messages)
    _summarize_decision(
        is_passthrough=ctx["is_passthrough"],
        skip_summary_for_tools=skip_summary_for_tools,
        plan=ctx["plan"],
        is_summary_enabled=ctx["is_summary_enabled"],
        req_id=req_id,
    )
    if (
        (not ctx["is_passthrough"])
        and (not skip_summary_for_tools)
        and ctx["is_summary_enabled"]
        and ctx["plan"].should
    ):
        # V2: delegate to SummarizationFilter (wired May 2026)
        from ..filters.summarization import SummarizationFilter
        from ..orchestrator.filter import FilterExecutionContext

        sf = SummarizationFilter()
        sfctx = FilterExecutionContext(
            req_id=req_id,
            upstream_model=ctx["summary_model"],
            upstream_payload=dict(payload, user_id=user_id, conv_id=conv_id),
        )
        sfctx.metadata.update(
            {
                "route": ctx["route"],
                "plan": ctx["plan"],
                "custom_prompt_type": ctx["custom_prompt_type"],
                "custom_prompt_text": ctx["custom_prompt_text"],
                "pinned_head_n": ctx["pinned_head_n"],
                "ctx_eff": ctx["ctx_eff"],
                "is_summary_enabled": ctx["is_summary_enabled"],
            }
        )

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
    payload,
    upstream_model,
    repacked_messages,
    route_plan: RoutePlan,
    max_tokens_req,
    ctx_eff,
    req_id,
    preserved_overrides=None,
):
    upstream_payload = dict(payload)
    upstream_payload["_original_model"] = upstream_payload.get("model", payload.get("model", ""))
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
    # A balancing selector reroutes to the target route from scratch. The
    # policy can nevertheless name explicit source overrides (for example
    # reasoning effort) that intentionally win over the target's defaults.
    if preserved_overrides:
        overrides.update(preserved_overrides)
    if overrides:
        from ..overrides import apply_overrides

        applied = apply_overrides(upstream_payload, overrides)
        if applied:
            for key, old_val, new_val in applied:
                _exec.emit_override(req_id, param=key, old_value=old_val, new_value=new_val)
    prompt_tokens_for_log = _count_tokens_safe(repacked_messages)
    _clamp_max_tokens_upstream(
        payload=upstream_payload,
        max_tokens_req=max_tokens_req,
        ctx_eff=ctx_eff,
        prompt_tokens_for_log=prompt_tokens_for_log,
    )
    return upstream_payload


def _log_upstream_request(upstream_payload, ctx, did_summarize, req_id):
    summarize_request_payload(upstream_payload)
    _exec.emit_repacked(
        req_id,
        did_summarize,
        ctx["is_passthrough"],
        f"{ctx['route_upstream_url']}/v1/chat/completions",
        _count_tokens_safe(upstream_payload.get("messages", [])),
        adjusted_max_tokens=upstream_payload.get("max_tokens"),
    )


def _runtime_attempt(route, route_name: str, model: str, endpoint_url: str, request_timeout: float) -> Dict[str, Any]:
    """Describe an in-flight upstream target without exposing request headers."""
    return {
        "route": route_name or getattr(route, "name", ""),
        "model": model,
        "endpoint_url": endpoint_url,
        "request_timeout": request_timeout,
    }


def _cached_tokens_from_response(response: Response) -> int | None:
    """Extract standard upstream cache accounting from a non-stream response."""
    try:
        body = json.loads(response.body)
        details = (body.get("usage") or {}).get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
        return int(cached) if cached is not None else None
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _prompt_tokens_from_response(response: Response) -> int | None:
    """Extract final upstream prompt usage for idle-reservation sizing."""
    try:
        body = json.loads(response.body)
        prompt = (body.get("usage") or {}).get("prompt_tokens")
        return int(prompt) if prompt is not None else None
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _completion_tokens_from_response(response: Response) -> int | None:
    """Extract upstream decode usage for compaction-pattern detection."""
    try:
        body = json.loads(response.body)
        completion = (body.get("usage") or {}).get("completion_tokens")
        return int(completion) if completion is not None else None
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _setup_fallback(route_plan: RoutePlan, req_id: str):
    """Materialize retry state from the compiled plan, not raw route config."""
    fallback_attempts = []
    # Passthrough routes forward directly — no fallback chain
    if route_plan.settings.passthrough_enabled:
        return fallback_attempts
    if len(route_plan.fallback_attempts) > 1:
        _exec.emit_fallback_chain(
            req_id,
            route_plan.settings.fallback_chain,
            route_plan.fallback_attempts,
        )
        fallback_attempts = list(route_plan.fallback_attempts)
    return fallback_attempts


def _build_pipeline_if_configured(route):
    """Build V2 Pipeline from canonical route filters configuration."""
    if not (route and hasattr(route, "filters") and route.filters):
        return None
    from ..orchestrator.pipeline import Pipeline

    return Pipeline.from_route_config(
        route.filters,
        api_key=getattr(route, "api_key", None),
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
                _reasoning_content = msg_data.get("reasoning_content") or msg_data.get("reasoning") or ""
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


def _apply_filtered_response(response_json: Dict[str, Any], response: Any) -> None:
    """Make the filtered first choice authoritative, including removed fields.

    Updating only non-empty fields resurrects upstream actions after RLS/TLS
    suppress them. Preserve the provider envelope/usage, but replace the message
    state and terminal reason with the post-filter result.
    """
    if response is None:
        return
    choices = response_json.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return
    choice = choices[0]
    message = choice.setdefault("message", {})
    message["content"] = response.content or ""
    tool_calls = getattr(response, "tool_calls", None)
    finish_reason = getattr(response, "finish_reason", None)
    if tool_calls:
        message["tool_calls"] = tool_calls
        # Normalize successful tool completions, but retain genuine truncation
        # or filtering reasons even when partial tool calls are present.
        choice["finish_reason"] = finish_reason if finish_reason not in (None, "stop") else "tool_calls"
    else:
        message.pop("tool_calls", None)
        choice["finish_reason"] = finish_reason if finish_reason not in (None, "tool_calls") else "stop"
    reasoning = getattr(response, "reasoning_content", "") or ""
    if reasoning:
        message["reasoning_content"] = reasoning
        if "reasoning" in message:
            message["reasoning"] = reasoning
    else:
        message.pop("reasoning_content", None)
        message.pop("reasoning", None)


async def process_chat_request(payload, headers, req_id, client_ip=None) -> Response | AsyncIterator[bytes]:
    """Process a chat completion request with full orchestration."""
    # This monotonic ingress mark owns both the hard admission budget and the
    # optional first-token target. It is intentionally independent of client
    # timeouts, headers, and wall-clock adjustments.
    request_arrival_monotonic = time.monotonic()
    client_model = payload.get("model")
    if not isinstance(client_model, str) or not client_model:
        return JSONResponse({"error": {"message": "Missing or invalid model"}}, status_code=400)

    # Resolve and authorize before request transcript logging, filtering, raw
    # capture, or any upstream work. ``api_key`` remains upstream-only;
    # ``api_keys`` are client credentials.
    route, model = resolve_route(client_model, req_id=req_id)
    from ..auth import (
        authentication_error_response,
        bearer_token,
        client_access_error_response,
        is_authorized,
        is_request_client_allowed,
    )

    if route is not None and not is_request_client_allowed(client_ip, headers, mode=route.client_access):
        return client_access_error_response()
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

    # Authentication belongs to the public/requested route. Runtime balance
    # selection happens only after it succeeds, then the concrete target owns
    # admission, upstream credentials, filters and all execution settings.
    requested_route = route
    resolved_client_model = model
    from ..admission import (
        FallbackAdmission,
        get_route_admission_controller,
        parse_deadline_policy,
    )
    from ..app import get_event_dispatcher, get_route_status_registry
    from ..circuit_breaker import get_circuit_breaker_registry
    from ..routing import get_session_affinity_registry
    from ..routing.balancing import (
        estimate_promotion_benefit,
        parse_balance_wait_policy,
        parse_capability_enforcement,
        parse_compaction_handoff_policy,
        parse_reservation_policy,
    )
    from ..routing.requirements import derive_request_requirements
    from ..routing.resources import backend_resource_key

    affinity_registry = get_session_affinity_registry()
    admission_controller = get_route_admission_controller()
    circuit_registry = get_circuit_breaker_registry()
    dispatcher = get_event_dispatcher()
    status_registry = get_route_status_registry()
    affinity_candidates = affinity_registry.candidate_keys(
        route_name=requested_route.name,
        headers=headers,
        payload=payload,
    )
    request_requirements = derive_request_requirements(
        payload,
        route_overrides=requested_route.overrides,
    )
    compaction_scope_key = affinity_registry.digest(
        route_name=requested_route.name,
        kind="compaction-source",
        value={
            "client_ip": client_ip or "unknown",
            "credential": headers.get("authorization") or headers.get("x-api-key") or "anonymous",
        },
    )
    # Resolve candidate order before selection. A stale assignment is harmless:
    # it is used only if its target remains in this route's current target set.
    candidate_states: dict[str, str] = {}
    declared_selection = None
    selection_started_at = request_arrival_monotonic
    selection_waited = False
    selection_retry_count = 0
    try:
        reservation_policy = parse_reservation_policy(
            requested_route.balance,
            route_name=requested_route.name,
        )
        enforce_capabilities = parse_capability_enforcement(
            requested_route.balance,
            route_name=requested_route.name,
        )
        wait_policy = parse_balance_wait_policy(
            requested_route.balance,
            route_name=requested_route.name,
        )
        compaction_handoff_policy = parse_compaction_handoff_policy(
            requested_route.balance,
            route_name=requested_route.name,
        )
        deadline_policy = parse_deadline_policy(requested_route.deadline_policy)
        # Resolve once without runtime predicates only to identify the complete
        # eligible target vocabulary for an existing session assignment.
        declared_selection = resolve_execution_route(requested_route)
        affinity = affinity_registry.lookup(
            affinity_candidates,
            allowed_routes=[candidate.route.name for candidate in declared_selection.alternatives],
        )
        compaction_handoff = None
        if affinity.route_name is None and compaction_handoff_policy.enabled:
            allowed_routes = [candidate.route.name for candidate in declared_selection.alternatives]
            compaction_handoff = affinity_registry.consume_compaction_continuation(
                source_route_name=requested_route.name,
                source_scope_key=compaction_scope_key,
                payload=payload,
                allowed_routes=allowed_routes,
            )
            handoff_state = "compaction_continuation"
            if compaction_handoff is None:
                compaction_handoff = affinity_registry.consume_compaction_candidate(
                    source_route_name=requested_route.name,
                    source_scope_key=compaction_scope_key,
                    new_prompt_tokens=request_requirements.prompt_tokens_est,
                    allowed_routes=allowed_routes,
                    min_prompt_reduction_ratio=compaction_handoff_policy.min_prompt_reduction_ratio,
                )
                handoff_state = "compaction_handoff"
            if compaction_handoff is not None:
                from ..routing.affinity import AffinityLookup

                affinity = AffinityLookup(
                    route_name=compaction_handoff.route_name,
                    state=handoff_state,
                    owner_key=compaction_handoff.owner_key,
                )
        service_priority = int(requested_route.routing_priority or 0)
        reservation_states: dict[str, str] = {}
        reservation_details: dict[str, Any] = {}
        queue_budget = max(0.0, float(requested_route.queue_timeout or 0.0))
        selection_deadline = selection_started_at + queue_budget

        def eligible(candidate, *, bypass_reservation_for: str | None = None):
            requirement_reason = request_requirements.incompatibility_reason(
                candidate,
                safety_margin_tokens=SAFETY_MARGIN_TOK,
                enforce_capabilities=enforce_capabilities,
            )
            if requirement_reason is not None:
                candidate_states[candidate.name] = requirement_reason
                return False
            resource = backend_resource_key(candidate)
            resource_name = str(resource) if resource is not None else None
            reservation = affinity_registry.reservation_status(
                resource_key=resource_name,
                owner_key=affinity.owner_key,
                routing_priority=service_priority,
            )
            reservation_states[candidate.name] = reservation.state
            reservation_details[candidate.name] = reservation
            circuit_available = circuit_registry.is_available(
                route_name=candidate.name,
                enabled=bool(candidate.circuit_breaker_enabled),
                recovery_timeout=float(candidate.recovery_timeout),
                resource_key=resource_name,
            )
            capacity_available = admission_controller.has_available_capacity(
                route_name=candidate.name,
                max_concurrent=candidate.max_concurrent,
                resource_key=resource_name,
            )
            if not circuit_available:
                candidate_states[candidate.name] = "circuit_open"
            elif not capacity_available:
                candidate_states[candidate.name] = "capacity_unavailable"
            elif reservation.state in {
                "reserved_by_higher_priority",
                "reserved_by_other_session",
            }:
                candidate_states[candidate.name] = (
                    "reservation_bypassed" if candidate.name == bypass_reservation_for else reservation.state
                )
            else:
                candidate_states[candidate.name] = "eligible"
            return candidate_states[candidate.name] in {
                "eligible",
                "reservation_bypassed",
            }

        affinity_resource = next(
            (
                backend_resource_key(candidate.route)
                for candidate in declared_selection.alternatives
                if candidate.route.name == affinity.route_name
            ),
            None,
        )
        affinity_reservation = affinity_registry.reservation_status(
            resource_key=str(affinity_resource) if affinity_resource is not None else None,
            owner_key=affinity.owner_key,
            routing_priority=service_priority,
        )
        promotion_estimate: dict[str, Any] | None = None

        while True:
            candidate_states.clear()
            reservation_states.clear()
            reservation_details.clear()
            try:
                unpinned_selection = resolve_execution_route(
                    requested_route,
                    is_eligible=eligible,
                )
                # A live lease retains continuity. Once it expires, re-evaluate
                # normal target order. If configured, measured performance may
                # justify doing so before expiry, but only at this request boundary.
                if affinity.route_name and (affinity_reservation.state == "owner" or compaction_handoff is not None):
                    pinned_selection = resolve_execution_route(
                        requested_route,
                        preferred_route_name=affinity.route_name,
                        is_eligible=eligible,
                    )
                    current_is_eligible = pinned_selection.selected_route.name == affinity.route_name
                    may_promote = reservation_policy.promote_when_beneficial and compaction_handoff is None
                    if compaction_handoff is not None:
                        promotion_estimate = {"decision": "deferred_for_compaction_handoff"}
                    elif not may_promote:
                        promotion_estimate = {"decision": "disabled"}
                    elif not current_is_eligible:
                        promotion_estimate = {"decision": "current_target_unavailable"}
                        may_promote = False
                    elif unpinned_selection.selected_route.name == affinity.route_name:
                        promotion_estimate = {
                            "decision": "no_higher_ranked_eligible_target",
                        }
                        may_promote = False
                    if may_promote:
                        current_route = next(
                            (c.route for c in declared_selection.alternatives if c.route.name == affinity.route_name),
                            None,
                        )
                        completion_budget = (
                            request_requirements.requested_completion_tokens
                            or getattr(requested_route, "max_tokens", None)
                            or getattr(unpinned_selection.selected_route, "max_tokens", None)
                        )
                        if current_route is not None and status_registry is not None:
                            promotion_estimate = estimate_promotion_benefit(
                                prompt_tokens=request_requirements.prompt_tokens_est,
                                cached_prompt_tokens=affinity.cached_prompt_tokens,
                                completion_tokens=completion_budget,
                                current_metrics=status_registry.promotion_metrics(
                                    current_route.name,
                                ),
                                candidate_metrics=status_registry.promotion_metrics(
                                    unpinned_selection.selected_route.name,
                                ),
                            )
                        else:
                            promotion_estimate = {"decision": "insufficient_metrics"}
                    if may_promote and promotion_estimate and promotion_estimate.get("decision") == "promote":
                        selection = unpinned_selection
                        selection_reason = "promotion_beneficial"
                    else:
                        selection = pinned_selection
                        selection_reason = (
                            affinity.state
                            if compaction_handoff is not None
                            and selection.selected_route.name == compaction_handoff.route_name
                            else "compaction_handoff_target_unavailable"
                            if compaction_handoff is not None
                            else "reservation_affinity"
                            if selection.selected_route.name == affinity.route_name
                            else "reservation_affinity_unavailable"
                        )
                else:
                    selection = unpinned_selection
                    selection_reason = (
                        "promotion"
                        if (affinity.route_name and selection.selected_route.name != affinity.route_name)
                        else "waited_for_capacity"
                        if selection_waited
                        else "ranked_eligible"
                    )
                break
            except NoEligibleBalanceTarget:
                waitable_states = {
                    "capacity_unavailable",
                    "reserved_by_higher_priority",
                    "reserved_by_other_session",
                }
                waitable = any(state in waitable_states for state in candidate_states.values())
                remaining = selection_deadline - time.monotonic()
                if declared_selection.balanced and waitable and remaining > 0:
                    selection_waited = True
                    selection_retry_count += 1
                    await asyncio.sleep(min(wait_policy.retry_interval_seconds, remaining))
                    continue

                # Reservations protect an idle resource, not an absolute
                # entitlement. If the queue budget is exhausted, use the least
                # costly still-idle reservation rather than return a selector
                # error. Physical capacity and hard compatibility stay strict.
                blocked = [
                    candidate
                    for candidate in declared_selection.alternatives
                    if candidate_states.get(candidate.route.name)
                    in {
                        "reserved_by_higher_priority",
                        "reserved_by_other_session",
                    }
                ]
                if declared_selection.balanced and wait_policy.allow_reservation_bypass and blocked:

                    def bypass_cost(candidate):
                        reservation = reservation_details[candidate.route.name]
                        return (
                            1 if reservation.mode == "session" else 0,
                            reservation.routing_priority or 0,
                            reservation.context_ratio or 0.0,
                            reservation.expires_in_seconds or 0.0,
                            candidate.route.name,
                        )

                    bypass = min(blocked, key=bypass_cost)
                    candidate_states.clear()
                    selection = resolve_execution_route(
                        requested_route,
                        is_eligible=lambda candidate: eligible(
                            candidate,
                            bypass_reservation_for=bypass.route.name,
                        ),
                    )
                    selection_reason = "reservation_bypass_after_wait"
                    break
                raise
    except NoEligibleBalanceTarget as exc:
        alternatives = (
            [candidate.route.name for candidate in declared_selection.alternatives]
            if declared_selection is not None
            else []
        )
        waitable = any(
            state
            in {
                "capacity_unavailable",
                "reserved_by_higher_priority",
                "reserved_by_other_session",
            }
            for state in candidate_states.values()
        )
        capacity_expired = waitable and float(requested_route.queue_timeout or 0.0) > 0
        _exec.emit_balance_rejected(
            req_id,
            requested_route=requested_route.name,
            alternatives=alternatives,
            candidate_states=candidate_states,
            required_capabilities=list(request_requirements.required_capabilities),
            enforce_capabilities=enforce_capabilities,
            prompt_tokens_est=request_requirements.prompt_tokens_est,
            requested_completion_tokens=request_requirements.requested_completion_tokens,
            selection_waited=selection_waited,
            selection_retry_count=selection_retry_count,
            status=429 if capacity_expired else 503,
            dispatcher=dispatcher,
        )
        if capacity_expired:
            return JSONResponse(
                {
                    "error": {
                        "message": "All eligible backend routes remained busy until the admission timeout.",
                        "type": "route_capacity_exceeded",
                        "code": "route_capacity_exceeded",
                    }
                },
                status_code=429,
                headers={"Retry-After": "1"},
            )
        return JSONResponse(
            {"error": {"message": str(exc), "type": "route_selection_error"}},
            status_code=503,
        )
    except (ValueError, RuntimeError) as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "type": "route_selection_error"}},
            status_code=503,
        )
    route = selection.selected_route
    admission_queue_timeout = requested_route.queue_timeout
    if float(requested_route.queue_timeout or 0.0) > 0:
        admission_queue_timeout = max(0.0, selection_deadline - time.monotonic())
    model = selection.selected_model or resolved_client_model
    first_token_target_at = (
        selection_started_at + deadline_policy.first_token_budget_seconds
        if deadline_policy.enabled and deadline_policy.first_token_budget_seconds is not None
        else None
    )

    def estimate_route_ttft(route_name: str) -> float | None:
        if not deadline_policy.enabled or status_registry is None:
            return None
        return status_registry.estimated_ttft_ms(
            route_name,
            min_samples=deadline_policy.min_samples,
        )

    estimated_ttft_ms = estimate_route_ttft(route.name)
    attempt_routes = selection.compile_attempt_routes(
        routes_by_name={candidate.name: candidate for candidate in USER_ROUTES},
        defaults=DEFAULTS,
    )
    # Direct routes retain their established behaviour. Capability/context
    # eligibility is a selector policy: only balance alternatives (including
    # their pre-output retry paths) are filtered here.
    if selection.balanced:
        attempt_routes = tuple(
            (candidate, candidate_model)
            for candidate, candidate_model in attempt_routes
            if request_requirements.incompatibility_reason(
                candidate,
                safety_margin_tokens=SAFETY_MARGIN_TOK,
                enforce_capabilities=enforce_capabilities,
            )
            is None
        )

    # Admission happens after authentication but before logging/capture,
    # filtering, summarization, and upstream allocation.  This makes a busy
    # route cheap to reject and ensures a streaming slot stays held until the
    # ASGI body generator is actually closed.
    if selection.balanced:
        _exec.emit_balance_decision(
            req_id,
            requested_route=selection.requested_route.name,
            selected_route=selection.selected_route.name,
            reroute_chain=list(selection.reroute_chain),
            alternatives=[candidate.route.name for candidate in selection.alternatives],
            affinity_state=(
                "selector_fallback"
                if selection.selected_via_selector_fallback
                else "preempted"
                if affinity.route_name and affinity.route_name != selection.selected_route.name
                else affinity.state
            ),
            selected_model=selection.selected_model,
            selected_upstream_url=(selection.selected_route.upstream_url or UPSTREAM_BASE_URL),
            preserved_override_keys=sorted(selection.preserved_overrides),
            preserved_filter_names=sorted(selection.preserved_filters),
            reservation_state=reservation_states.get(
                selection.selected_route.name,
                affinity_reservation.state,
            ),
            selection_reason=selection_reason,
            candidate_states=candidate_states,
            required_capabilities=list(request_requirements.required_capabilities),
            enforce_capabilities=enforce_capabilities,
            prompt_tokens_est=request_requirements.prompt_tokens_est,
            requested_completion_tokens=request_requirements.requested_completion_tokens,
            selection_waited=selection_waited,
            selection_retry_count=selection_retry_count,
            promotion_estimate=promotion_estimate,
            dispatcher=dispatcher,
        )
    resource = backend_resource_key(route)
    admission_lease = await admission_controller.acquire(
        req_id=req_id,
        route_name=route.name,
        max_concurrent=route.max_concurrent,
        # Capacity belongs to the concrete target/backend; waiting tolerance
        # and service class belong to the client-facing requested route.
        queue_timeout=admission_queue_timeout,
        routing_priority=service_priority,
        queue_aging=requested_route.queue_aging,
        deadline_policy=requested_route.deadline_policy,
        first_token_target_at=first_token_target_at,
        estimated_ttft_ms=estimated_ttft_ms,
        service_route_name=requested_route.name,
        resource_key=str(resource) if resource is not None else None,
        dispatcher=dispatcher,
    )
    if route.max_concurrent is not None and admission_lease is None:
        return JSONResponse(
            {
                "error": {
                    "message": "Route is at capacity; try again later.",
                    "type": "route_capacity_exceeded",
                    "code": "route_capacity_exceeded",
                }
            },
            status_code=429,
            headers={"Retry-After": "1"},
        )

    fallback_admission = FallbackAdmission(
        controller=admission_controller,
        req_id=req_id,
        lease=admission_lease,
        route_name=route.name,
        max_concurrent=route.max_concurrent,
        resource_key=str(resource) if resource is not None else None,
        queue_timeout=admission_queue_timeout,
        routing_priority=service_priority,
        queue_aging=requested_route.queue_aging,
        deadline_policy=requested_route.deadline_policy,
        first_token_target_at=first_token_target_at,
        ttft_estimator=estimate_route_ttft,
        service_route_name=requested_route.name,
        dispatcher=dispatcher,
    )

    if selection.balanced:
        affinity_assignment_keys = affinity_registry.assignment_keys(
            route_name=requested_route.name,
            headers=headers,
            payload=payload,
        )
        if compaction_handoff is not None:
            affinity_registry.forget_owner_assignments(compaction_handoff.owner_key)
            new_owner_key = affinity_assignment_keys[0] if affinity_assignment_keys else None
            new_resource_key = str(resource) if resource is not None else None
            if new_resource_key == compaction_handoff.resource_key:
                affinity_registry.transfer_reservation(
                    resource_key=new_resource_key,
                    previous_owner_key=compaction_handoff.owner_key,
                    new_owner_key=new_owner_key,
                )
            else:
                affinity_registry.release_reservation(
                    resource_key=compaction_handoff.resource_key,
                    owner_key=compaction_handoff.owner_key,
                )
            assignment_owner_key = new_owner_key
        else:
            if affinity.route_name and affinity.route_name != selection.selected_route.name:
                affinity_registry.release_reservation(
                    resource_key=(str(affinity_resource) if affinity_resource is not None else None),
                    owner_key=affinity.owner_key,
                )
            assignment_owner_key = affinity.owner_key
        affinity_owner_key = affinity_registry.assign(
            affinity_assignment_keys,
            route_name=selection.selected_route.name,
            ttl_seconds=reservation_policy.affinity_ttl_seconds,
            owner_key=assignment_owner_key,
        )
    else:
        affinity_assignment_keys = ()
        affinity_owner_key = None

    def complete_idle_reservation(
        prompt_tokens: Any,
        cached_prompt_tokens: Any = None,
        completion_tokens: Any = None,
    ) -> None:
        """Update affinity, compaction signal and idle lease after success."""
        if not selection.balanced:
            return
        effective_prompt_tokens = (
            prompt_tokens
            if isinstance(prompt_tokens, int) and prompt_tokens > 0
            else request_requirements.prompt_tokens_est
        )
        if compaction_handoff_policy.enabled:
            handoff_state = affinity_registry.record_compaction_candidate(
                source_route_name=requested_route.name,
                source_scope_key=compaction_scope_key,
                target_route_name=selection.selected_route.name,
                resource_key=str(resource) if resource is not None else None,
                owner_key=affinity_owner_key,
                prompt_tokens=effective_prompt_tokens,
                completion_tokens=completion_tokens,
                ctx_len=ctx.get("ctx_eff", route.ctx_len),
                max_age_seconds=compaction_handoff_policy.max_age_seconds,
                min_context_ratio=compaction_handoff_policy.min_context_ratio,
                min_completion_tokens=compaction_handoff_policy.min_completion_tokens,
            )
            if compaction_handoff is not None and affinity.state == "compaction_handoff":
                affinity_registry.record_compaction_continuation(
                    source_route_name=requested_route.name,
                    source_scope_key=compaction_scope_key,
                    payload=payload,
                    target_route_name=selection.selected_route.name,
                    resource_key=str(resource) if resource is not None else None,
                    owner_key=affinity_owner_key,
                    max_age_seconds=compaction_handoff_policy.max_age_seconds,
                )
        else:
            handoff_state = "disabled"
        if not reservation_policy.enabled:
            _exec.emit_reservation(
                req_id,
                route=selection.selected_route.name,
                resource=str(resource) if resource is not None else None,
                state="disabled",
                compaction_handoff=handoff_state,
                dispatcher=dispatcher,
            )
            return
        reservation = affinity_registry.reserve(
            resource_key=str(resource) if resource is not None else None,
            owner_key=affinity_owner_key,
            route_name=selection.selected_route.name,
            routing_priority=service_priority,
            mode=reservation_policy.mode,
            timeout_seconds=reservation_policy.timeout_seconds,
            prompt_tokens=prompt_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            min_prompt_tokens=reservation_policy.min_prompt_tokens,
            min_cached_prompt_tokens=reservation_policy.min_cached_prompt_tokens,
            # ``ctx_eff`` is the effective request window after inherited
            # route settings and the client max-token request are reconciled.
            # It is a more accurate occupancy denominator than the raw route
            # declaration and remains available by callback execution time.
            ctx_len=ctx.get("ctx_eff", route.ctx_len),
            min_seconds=reservation_policy.min_seconds,
            max_seconds=reservation_policy.max_seconds,
            min_context_ratio=reservation_policy.min_context_ratio,
            max_context_ratio=reservation_policy.max_context_ratio,
        )
        _exec.emit_reservation(
            req_id,
            route=selection.selected_route.name,
            resource=str(resource) if resource is not None else None,
            state=reservation.state,
            mode=reservation_policy.mode,
            duration_seconds=reservation.expires_in_seconds,
            prompt_tokens=prompt_tokens,
            cached_prompt_tokens=cached_prompt_tokens,
            min_prompt_tokens=reservation_policy.min_prompt_tokens,
            min_cached_prompt_tokens=reservation_policy.min_cached_prompt_tokens,
            context_ratio=(
                min(
                    1.0,
                    max(
                        0.0,
                        int(prompt_tokens) / max(1, int(ctx.get("ctx_eff", route.ctx_len) or 1)),
                    ),
                )
                if isinstance(prompt_tokens, (int, float))
                else None
            ),
            compaction_handoff=handoff_state,
            dispatcher=dispatcher,
        )

    _exec.emit_request_start(req_id, stream=payload.get("stream", False))
    t_start = time.perf_counter()
    try:
        user_id, conv_id, client_model, messages, stream, max_tokens_req = _parse_request(payload, headers, req_id)
    except ValueError as e:
        await fallback_admission.release()
        return JSONResponse({"error": {"message": str(e)}}, status_code=400)
    ctx = _resolve_route_context(
        client_model,
        messages,
        payload,
        max_tokens_req,
        req_id,
        route=route,
        model=model,
        requested_route_name=selection.requested_route.name,
        reroute_chain=selection.reroute_chain,
        attempt_routes=attempt_routes,
        preserved_filters=selection.preserved_filters,
    )

    circuit_attempt = circuit_registry.allow(
        route_name=ctx["route"].name,
        enabled=bool(ctx["route"].circuit_breaker_enabled),
        recovery_timeout=float(ctx["route"].recovery_timeout),
        req_id=req_id,
        dispatcher=dispatcher,
        resource_key=str(resource) if resource is not None else None,
    )
    if ctx["route"].circuit_breaker_enabled and circuit_attempt is None:
        # A configured fallback is still a useful service path when the
        # primary circuit is open. RoutePlan always includes the primary as
        # item zero, so subsequent entries are fallback model attempts.
        fallback_attempts = ctx["route_plan"].fallback_attempts[1:]
        if fallback_attempts:
            fallback = fallback_attempts[0]
            primary = ctx["route_plan"].fallback_attempts[0]
            _exec.emit_fallback(
                req_id,
                primary,
                fallback,
                reason="circuit_open",
                attempt=1,
                total_attempts=len(ctx["route_plan"].fallback_attempts),
                dispatcher=dispatcher,
            )
            ctx = dict(ctx)
            ctx["upstream_model"] = fallback.model
            ctx["url"] = fallback.endpoint_url
            ctx["request_timeout"] = fallback.request_timeout
            ctx["route_headers"] = dict(fallback.upstream_headers)
            if not await fallback_admission.move_to(fallback.route):
                await fallback_admission.release()
                return JSONResponse(
                    {
                        "error": {
                            "message": "Fallback route is at capacity; try again later.",
                            "type": "route_capacity_exceeded",
                            "code": "route_capacity_exceeded",
                        }
                    },
                    status_code=429,
                    headers={"Retry-After": "1"},
                )
            circuit_attempt = None
        else:
            await fallback_admission.release()
            return JSONResponse(
                {
                    "error": {
                        "message": "Upstream route circuit is open; try again later.",
                        "type": "upstream_unavailable",
                        "code": "circuit_open",
                    }
                },
                status_code=503,
                headers={"Retry-After": str(max(1, int(ctx["route"].recovery_timeout)))},
            )

    def circuit_success() -> None:
        circuit_registry.record_success(circuit_attempt, req_id=req_id, dispatcher=dispatcher)

    def circuit_failure() -> None:
        circuit_registry.record_failure(
            circuit_attempt,
            req_id=req_id,
            failure_threshold=int(ctx["route"].failure_threshold),
            dispatcher=dispatcher,
        )

    # ── Summarization: V2 Pipeline (non-streaming handled in process_non_streaming_request) ──
    # For streaming: request processing happens in streaming handler
    # For non-streaming: request processing happens in process_non_streaming_request
    repacked_messages, did_summarize, summary_tokens = await _handle_summarization(
        messages=messages, payload=payload, user_id=user_id, conv_id=conv_id, ctx=ctx, req_id=req_id
    )
    upstream_payload = _build_upstream_payload(
        payload,
        ctx["upstream_model"],
        repacked_messages,
        ctx["route_plan"],
        max_tokens_req,
        ctx["ctx_eff"],
        req_id,
        selection.preserved_overrides,
    )

    # O12: Emit raw request capture event (post-route-resolution, pre-filter-chain)
    _exec.emit_request_capture(
        req_id=req_id,
        raw_body=upstream_payload,
        client_model=client_model,
        resolved_route=ctx["route"].name,
        upstream_model=ctx["upstream_model"],
        upstream_url=ctx["url"],
        route_hierarchy=getattr(ctx["route"], "_route_hierarchy", []),
        dispatcher=get_event_dispatcher(),
    )

    _log_upstream_request(upstream_payload, ctx, did_summarize, req_id)
    client = await http_client(ctx["request_timeout"])
    fallback_attempts = _setup_fallback(ctx["route_plan"], req_id)
    _exec.emit_request_route(req_id, stream, ctx["route"].name, filters=list(ctx["route_plan"].enabled_filters))
    route_headers = ctx["route_plan"].build_upstream_headers()
    pipeline = ctx["route_plan"].build_pipeline()
    primary_failed = False

    async def move_to_fallback(attempt: Any) -> bool:
        nonlocal primary_failed
        primary_failed = True
        fallback_route = getattr(attempt, "route", attempt)
        result = fallback_admission.move_to(fallback_route)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    # Phase P5: wire dispatcher through streaming handler call chain
    dispatcher = get_event_dispatcher()
    if stream:
        result = process_streaming_request(
            url=ctx["url"],
            client=client,
            payload=upstream_payload,
            route_headers=route_headers,
            route=ctx["route"],
            req_id=req_id,
            request_timeout=ctx["request_timeout"],
            fallback_attempts=fallback_attempts,
            visited_models=None,
            upstream_model=ctx["upstream_model"],
            is_passthrough=ctx["is_passthrough"],
            transform_reasoning_content=ctx["transform_reasoning_content"],
            add_empty_content_when_reasoning_only=ctx["add_empty_content_when_reasoning_only"],
            reasoning_placeholder=ctx["reasoning_placeholder"],
            t_start=t_start,
            pipeline=pipeline,
            enabled_filters=ctx["route_plan"].enabled_filters,
            dispatcher=dispatcher,
            on_upstream_success=(circuit_success if not primary_failed else None),
            on_upstream_failure=circuit_failure,
            on_cache_observation=(
                lambda cached: (
                    affinity_registry.record_cache_observation(
                        affinity_assignment_keys,
                        route_name=selection.selected_route.name,
                        cached_prompt_tokens=cached,
                    )
                    if selection.balanced
                    else None
                )
            ),
            on_completion=(complete_idle_reservation if selection.balanced else None),
            on_fallback_attempt=move_to_fallback,
        )
        return _release_admission_after_stream(result, fallback_admission)
    try:
        result = await process_non_streaming_request(
            url=ctx["url"],
            client=client,
            payload=upstream_payload,
            route_headers=route_headers,
            req_id=req_id,
            upstream_model=ctx["upstream_model"],
            fallback_attempts=fallback_attempts,
            t_start=t_start,
            did_summarize=did_summarize,
            route_name=ctx["route"].name,
            route=ctx["route"],
            pipeline=pipeline,
            enabled_filters=ctx["route_plan"].enabled_filters,
            request_timeout=ctx["request_timeout"],
            on_fallback_attempt=move_to_fallback,
        )
        if result.status_code >= 500:
            circuit_failure()
        elif not primary_failed:
            circuit_success()
        if selection.balanced:
            affinity_registry.record_cache_observation(
                affinity_assignment_keys,
                route_name=selection.selected_route.name,
                cached_prompt_tokens=_cached_tokens_from_response(result),
            )
            if result.status_code < 400:
                complete_idle_reservation(
                    _prompt_tokens_from_response(result),
                    _cached_tokens_from_response(result),
                    _completion_tokens_from_response(result),
                )
        return result
    finally:
        await fallback_admission.release()


async def process_non_streaming_request(
    url: str,
    client: httpx.AsyncClient,
    payload: Dict[str, Any],
    route_headers: Dict[str, str],
    req_id: str,
    upstream_model: str,
    fallback_attempts: Sequence[Any],
    t_start: float,
    did_summarize: bool = False,
    route_name: str = "",
    route=None,  # Route object for filters integration
    pipeline=None,
    enabled_filters: Sequence[str] | None = None,
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    visited_models: Set[str] | None = None,  # legacy caller compatibility
    fallback_attempt_offset: int = 0,
    fallback_total_attempts: int | None = None,
    on_fallback_attempt: Any = None,
    request_already_processed: bool = False,
) -> Response:
    """Process a non-streaming chat completion request.

    Args:
        url: Upstream API URL
        client: HTTPX async client
        payload: Request payload
        route_headers: Route-specific headers
        req_id: Request ID
        upstream_model: Primary model being used
        fallback_attempts: Fully resolved upstream retry targets.
        t_start: Start timestamp for timing calculations
        did_summarize: Whether summarization was performed
        route_name: Name of the route that handled the request

    Returns:
        FastAPI Response object
    """

    # ABSOLUTE_DEBUG: this log MUST appear if code is reached
    has_filters = bool(route and hasattr(route, "filters") and route.filters)
    filters_list = list(enabled_filters or [])

    _exec.emit_route_resolved(
        req_id,
        client_model="",
        resolved_route=route_name,
        model="",
        upstream_model="",
        summary_model="",
        passthrough_enabled=False,
        ctx_len=0,
        max_tokens_default=0,
        parent_routes=[],
        has_filters=has_filters,
        filters=filters_list,
    )

    # Prepare upstream payload for retry logic (filter chain can modify messages)
    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model

    # ── Process request through V2 Pipeline ──────────────────────────
    effective_payload = dict(payload)
    if pipeline is None:
        pipeline = _build_pipeline_if_configured(route)
    if pipeline and not request_already_processed:
        effective_payload = await pipeline.process_request(
            effective_payload,
            req_id,
            upstream_model,
            route_name=route_name,
            upstream_url=getattr(route, "upstream_url", None) or UPSTREAM_BASE_URL,
        )

    # Reset elapsed timer after filter chain (metrics measure upstream time, not overhead)
    t_start = time.perf_counter()
    total_attempts = fallback_total_attempts or max(1, len(fallback_attempts))
    current_attempt: Any = _runtime_attempt(
        route,
        route_name,
        upstream_model,
        url,
        request_timeout,
    )

    async def prepare_fallback_attempt(attempt: Any) -> bool:
        """Acquire capacity for a concrete fallback before contacting it."""
        if on_fallback_attempt is None:
            return True
        result = on_fallback_attempt(attempt.route)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def retry_transport_failure(reason: str, error: BaseException) -> Optional[Response]:
        """Retry a zero-response failure through the remaining route attempts."""
        for index, attempt in enumerate(fallback_attempts[1:], start=1):
            if not await prepare_fallback_attempt(attempt):
                continue
            _exec.emit_fallback(
                req_id,
                current_attempt,
                attempt,
                reason=reason,
                attempt=fallback_attempt_offset + index,
                total_attempts=total_attempts,
                error_type=type(error).__name__,
                error=str(error),
            )
            retry_payload = dict(effective_payload)
            retry_payload["model"] = attempt.model
            return await process_non_streaming_request(
                url=attempt.endpoint_url,
                client=client,
                payload=retry_payload,
                route_headers=dict(attempt.upstream_headers),
                req_id=req_id,
                upstream_model=attempt.model,
                fallback_attempts=list(fallback_attempts[index:]),
                t_start=t_start,
                did_summarize=did_summarize,
                route_name=attempt.route_name,
                route=attempt.route,
                # Keep the original source-route pipeline for response
                # normalization after a transport fallback. The request
                # phase already ran before the first upstream attempt and
                # must not be applied a second time.
                pipeline=pipeline,
                enabled_filters=enabled_filters,
                request_timeout=attempt.request_timeout,
                fallback_attempt_offset=fallback_attempt_offset + index,
                fallback_total_attempts=total_attempts,
                on_fallback_attempt=on_fallback_attempt,
                request_already_processed=True,
            )
        return None

    try:
        r = await _post_upstream(
            client,
            url,
            json=effective_payload,
            headers=route_headers,
            request_timeout=request_timeout,
        )

        if r.status_code >= 400:
            err_bytes = await r.aread()
            err_text = err_bytes.decode("utf-8", errors="replace")

            # O11: emit full error body (not truncated) so BodyCaptureConsumer
            # can capture high-fidelity data. Logger/formatters truncate their
            # projection if needed (P4: Event Data and Log Representation are separate).
            _exec.emit_upstream_error(
                req_id,
                r.status_code,
                url,
                route_name,
                upstream_model,
                err_text,
                request_payload=effective_payload,
            )

            # Dump full payload for debugging multimodal/tokenizer errors
            # PRESERVED for parity verification between __dumps__/ and __body_captures__/
            await dump_failed_payload(
                req_id,
                effective_payload,
                r.status_code,
                err_text,
                upstream_model=upstream_model,
                upstream_url=url,
                route=route_name,
            )

            # ── Retry by stripping last image on tokenizer error ──
            retry_response = await _retry_strip_last_image_non_streaming(
                effective_payload,
                err_text,
                route,
                req_id,
                client,
                url,
                route_headers,
                request_timeout,
            )
            if retry_response is not None:
                return retry_response

            # A 400 is a client request error (for example vLLM context
            # overflow). A fallback cannot repair it, so preserve the actual
            # upstream envelope instead of masking it behind another model.
            if _is_terminal_client_http_error(r.status_code, err_text):
                return _terminal_upstream_error_response(r.status_code, err_text)

            # The first compiled attempt is the primary. Each remaining
            # attempt owns its endpoint, model, headers, and deadline.
            failure_reason = "http_status"
            failure_status: int | None = r.status_code
            failure_error_type: str | None = None
            failure_error = err_text
            for index, attempt in enumerate(fallback_attempts[1:], start=1):
                if not await prepare_fallback_attempt(attempt):
                    continue
                _exec.emit_fallback(
                    req_id,
                    current_attempt,
                    attempt,
                    reason=failure_reason,
                    attempt=fallback_attempt_offset + index,
                    total_attempts=total_attempts,
                    status=failure_status,
                    error_type=failure_error_type,
                    error=failure_error,
                )
                retry_payload = dict(effective_payload)
                retry_payload["model"] = attempt.model
                # Retain the concrete attempted target for both the next
                # retry decision and a useful final upstream-error response.
                upstream_model = attempt.model
                url = attempt.endpoint_url
                route_headers = dict(attempt.upstream_headers)
                request_timeout = attempt.request_timeout
                current_attempt = attempt
                try:
                    r = await _post_upstream(
                        client,
                        url,
                        json=retry_payload,
                        headers=route_headers,
                        request_timeout=request_timeout,
                    )
                    if r.status_code < 400:
                        break
                    err_bytes = await r.aread()
                    err_text = err_bytes.decode("utf-8", errors="replace")
                    _exec.emit_upstream_error(
                        req_id,
                        r.status_code,
                        url,
                        route_name,
                        upstream_model,
                        err_text,
                        request_payload=retry_payload,
                    )
                    failure_reason = "http_status"
                    failure_status = r.status_code
                    failure_error_type = None
                    failure_error = err_text
                except Exception as exc:
                    failure_reason = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport_error"
                    failure_status = None
                    failure_error_type = type(exc).__name__
                    failure_error = str(exc)
                    continue

            # If still failed, return error response
            if r.status_code >= 400:
                return JSONResponse(
                    {
                        "error": {
                            "message": "Upstream error",
                            "details": err_text[:2000],
                            "route": route_name,
                            "upstream_model": upstream_model,
                            "url": url,
                            "status": r.status_code,
                        }
                    },
                    status_code=r.status_code,
                )
        # Account for all attempts, including filter-owned recovery calls.
        from ..streaming.accounting import ExecutionUsage

        _execution_usage = ExecutionUsage.empty()
        _execution_usage.add_attempt(0, r.json().get("usage"))

        # ── Apply response pipeline ────────────────────────────────
        processed_response = None
        if pipeline:

            async def _retry_upstream(p, *, request_timeout_override=None):
                p["model"] = upstream_model
                raw_usage = None
                try:
                    retry_response = await _post_upstream(
                        client,
                        url,
                        json=p,
                        headers=route_headers,
                        request_timeout=request_timeout
                        if request_timeout_override is None
                        else request_timeout_override,
                    )
                    retry_response.raise_for_status()
                    body = retry_response.json()
                    raw_usage = body.get("usage")
                    return body
                finally:
                    _execution_usage.add_attempt(_execution_usage.upstream_attempts, raw_usage)

            mock_response = _MockResponse_for_pipeline(r, model=upstream_model)
            from ..app import get_event_dispatcher

            try:
                processed_response = await pipeline.process_response(
                    mock_response,
                    payload,
                    req_id,
                    upstream_model,
                    route_name=getattr(route, "name", ""),
                    upstream_url=getattr(route, "upstream_url", None) or UPSTREAM_BASE_URL,
                    is_streaming=False,
                    upstream_caller=_retry_upstream,
                    context_metadata={"request_timeout": request_timeout},
                    dispatcher=get_event_dispatcher(),
                )
            except Exception as e:
                _exec.emit_pipeline_error(req_id, str(e))

        # Extract usage data from response for metrics
        response_json = r.json()
        _apply_filtered_response(response_json, processed_response)
        usage = response_json.get("usage") or {}

        _execution_usage.finalize()

        completion_tokens = usage.get("completion_tokens")
        prompt_tokens = usage.get("prompt_tokens")
        total_tokens = usage.get("total_tokens", (completion_tokens or 0) + (prompt_tokens or 0))

        # Extract cached_tokens from prompt_tokens_details
        prompt_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = prompt_details.get("cached_tokens")

        # If filter chain modified the response (e.g. nudge retry), estimate tokens from processed content
        # because the original usage only reflects the first (lazy) response, not the accumulated result
        if processed_response and hasattr(processed_response, "content") and processed_response.content:
            from ..token_counter import TokenCounter

            tc = TokenCounter()
            processed_content = processed_response.content or ""
            completion_tokens = tc.count_text(processed_content)
            # Also count tool_calls JSON arguments, if any
            if hasattr(processed_response, "tool_calls") and processed_response.tool_calls:
                for tc_call in processed_response.tool_calls:
                    fn = tc_call.get("function", {})
                    args = fn.get("arguments", "{}")
                    completion_tokens += tc.count_text(args)
            total_tokens = (prompt_tokens or 0) + completion_tokens
            # Reset cached_tokens since response was modified
            cached_tokens = None

        # Calculate elapsed time and record metrics
        if _execution_usage.recovery_count:
            # Upstream totals include rejected/retried work. Logical completion
            # tokens describe only the accepted, client-visible response.
            from ..token_counter import TokenCounter

            counter = TokenCounter()
            choice = (response_json.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            final_tokens = counter.count_text(message.get("content") or "")
            final_tokens += counter.count_text(message.get("reasoning_content") or message.get("reasoning") or "")
            for call in message.get("tool_calls") or []:
                final_tokens += counter.count_text((call.get("function") or {}).get("arguments") or "")
            _execution_usage.set_final_usage(prompt_tokens=prompt_tokens, completion_tokens=final_tokens)

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        # Use ExecutionUsage if available (Phase 1 internal accounting)
        if _execution_usage is not None:
            _record_final_metrics(
                {
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
                    "completion_tokens_source": "estimate" if _execution_usage.recovery_count else "usage",
                    "upstream_attempts": _execution_usage.upstream_attempts,
                    "usage_reported_attempts": _execution_usage.usage_reported_attempts,
                    "usage_complete": _execution_usage.usage_complete,
                    "recovery_count": _execution_usage.recovery_count,
                    "upstream_prompt_tokens": _execution_usage.upstream_prompt_tokens,
                    "upstream_completion_tokens": _execution_usage.upstream_completion_tokens,
                    "upstream_total_tokens": _execution_usage.upstream_total_tokens,
                    "cached_prompt_tokens": cached_tokens,
                },
                t_start=t_start,
                did_summarize=did_summarize,
                route_name=route_name,
                route=route,
            )
        else:
            # For non-streaming requests, set ttft_ms=0 so completion_tps uses full elapsed time
            # This gives us: completion_tps = completion_tokens / (elapsed / 1000.0)
            _record_final_metrics(
                {
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
                },
                t_start=t_start,
                did_summarize=did_summarize,
                route_name=route_name,
                route=route,
            )
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
            if processed_response and hasattr(processed_response, "tool_calls") and processed_response.tool_calls:
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
            if processed_response and hasattr(processed_response, "reasoning_content"):
                reasoning_text = processed_response.reasoning_content or ""
            elif processed_response is None:
                resp_body = r.json()
                choices = resp_body.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg_data = choices[0].get("message", {})
                    reasoning_text = msg_data.get("reasoning_content") or msg_data.get("reasoning") or ""

            _exec.emit_assistant(
                req_id,
                assistant_text if assistant_text else "",
                len(assistant_text),
                tool_calls=tc_names if tc_names else None,
                reasoning_content=reasoning_text,
                reasoning_length=len(reasoning_text),
                finish_reason=response_json.get("choices", [{}])[0].get("finish_reason"),
            )
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

        # Serialize the same normalized response used by metrics and logs.
        _exec.emit_http_out(req_id, r.status_code)
        return JSONResponse(content=response_json, status_code=r.status_code)
    except httpx.TimeoutException as exc:
        retry_response = await retry_transport_failure("timeout", exc)
        if retry_response is not None:
            return retry_response
        _exec.emit_timeout(req_id)
        # Record metrics even on timeout
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        _record_final_metrics(
            {
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
            },
            t_start=t_start,
            did_summarize=did_summarize,
            route_name=route_name,
            route=route,
        )
        return JSONResponse({"error": {"message": "Request timeout"}}, status_code=504)
    except Exception as e:
        retry_response = await retry_transport_failure("transport_error", e)
        if retry_response is not None:
            return retry_response
        import traceback

        _exec.emit_failed(req_id, str(e), url, route_name, upstream_model, traceback.format_exc())
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        _record_final_metrics(
            {
                "model": upstream_model,
                "req_id": req_id,
                "stream": False,
                "ttft_ms": None,
                "elapsed_ms": elapsed_ms,
                "completion_tokens": None,
                "prompt_tokens": None,
                "total_tokens": None,
                "finish_reason": str(e),
                "passthrough": False,
                "completion_tokens_source": "missing",
            },
            t_start=t_start,
            did_summarize=did_summarize,
            route_name=route_name,
            route=route,
        )
        return JSONResponse(
            {
                "error": {
                    "message": str(e),
                    "route": route_name,
                    "upstream_model": upstream_model,
                    "url": url,
                    "hint": "Check UPSTREAM_BASE_URL env var or route config upstream_url",
                }
            },
            status_code=500,
        )


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
        second_last_msg.get("role") == "assistant"
        and last_msg.get("role") == "tool"
        and (has_tool_calls or "tool_call_id" in last_msg)
    )


def _summarize_decision(
    is_passthrough: bool,
    skip_summary_for_tools: bool,
    plan: Any,
    is_summary_enabled: bool,
    req_id: str,
) -> None:
    """Log summary bypass decision."""
    if is_passthrough:
        _exec.emit_summary_bypassed(
            req_id=req_id,
            reason="passthrough_model",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif skip_summary_for_tools:
        _exec.emit_summary_bypassed(
            req_id=req_id,
            reason="memory_payload",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif not plan.should:
        _exec.emit_summary_bypassed(
            req_id=req_id,
            reason=plan.reason,
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )
    elif not is_summary_enabled:
        _exec.emit_summary_bypassed(
            req_id=req_id,
            reason="summary_disabled_in_config",
            prompt_tok_est=plan.prompt_tok_est or 0,
            threshold=plan.threshold or 0,
        )


def _clamp_max_tokens_upstream(
    payload: Dict, max_tokens_req: Optional[int], ctx_eff: int, prompt_tokens_for_log: Optional[int]
) -> None:
    """Clamp and set max_tokens for upstream request."""
    from ..config import DEFAULT_MAX_COMPLETION_TOKENS, SAFETY_MARGIN_TOK

    if DEFAULT_MAX_COMPLETION_TOKENS is not None:
        max_tokens_upstream = max(64, int(ctx_eff) - int(prompt_tokens_for_log or 0) - int(SAFETY_MARGIN_TOK))
        requested_out = (
            int(max_tokens_req)
            if isinstance(max_tokens_req, int) and max_tokens_req > 0
            else DEFAULT_MAX_COMPLETION_TOKENS
        )
        adjusted_out = min(requested_out, max_tokens_upstream)
        payload["max_tokens"] = adjusted_out

        if adjusted_out < (requested_out if isinstance(max_tokens_req, int) and max_tokens_req > 0 else 0):
            _exec.emit_max_tokens_clamped(
                req_id="",
                requested=requested_out,
                adjusted=adjusted_out,
                ctx_len=ctx_eff,
                prompt_tokens=prompt_tokens_for_log,
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

    route_hierarchy = getattr(route, "_route_hierarchy", [route_name]) if route else [route_name]

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
