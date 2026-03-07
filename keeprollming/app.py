from __future__ import annotations

import json
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import (
    MAIN_MODEL,
    SAFETY_MARGIN_TOK,
    SUMMARY_MODEL,
    SUMMARY_MODE,
    SUMMARY_CACHE_ENABLED,
    SUMMARY_CACHE_DIR,
    SUMMARY_CACHE_FINGERPRINT_MSGS,
    SUMMARY_FORCE_CONSOLIDATE,
    SUMMARY_CONSOLIDATE_WHEN_NEEDED,
    UPSTREAM_BASE_URL,
    resolve_profile_and_models,
)
from .logger import (
    BASIC_SNIP_CHARS,
    LOG_MODE,
    LOG_MODE_CHOICES,
    LOG_SNIP_CHARS,
    classify_messages,
    extract_last_user_text,
    log,
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

# ----------------------------
# Token counter
# ----------------------------
TOK = TokenCounter()

# Max chars for logging large payloads (input conversation, summary requests, etc.)
LOG_PAYLOAD_MAX_CHARS = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", "20000000"))

MAX_SSE_BYTES = 8000  # capture only the first N bytes of SSE bodies for logging


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
    entries = load_cache_entries(SUMMARY_CACHE_DIR, fingerprint)
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # startup: nothing special
    yield
    # shutdown
    await close_http_client()


app = FastAPI(lifespan=lifespan)


@app.post("/v1/chat/completions")
async def chat_completions(req: Request) -> Response:
    req_id = os.urandom(6).hex()
    payload = await req.json()
    headers = dict(req.headers)

    # Controllare se ci sono gli header necessari per il caching
    user_id = headers.get("x-librechat-user-id", "")
    conv_id = headers.get("x-librechat-conversation-id", "")
    msg_id = headers.get("x-librechat-message-id", "")
    parent_msg_id = headers.get("x-librechat-parent-message-id", "")
    
    # Se abbiamo gli header, li usiamo per il fingerprint
    has_headers = bool(user_id or conv_id)
    
    if LOG_MODE == "DEBUG":
        log("INFO", "request_received", header=headers, req_id=req_id, body_json=snip_json(payload))

    client_model = payload.get("model", MAIN_MODEL)
    profile, upstream_model, summary_model, is_passthrough = resolve_profile_and_models(client_model)

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": {"message": "Invalid payload: messages must be a list"}}, status_code=400)

    stream = bool(payload.get("stream", False))

    last_user = extract_last_user_text(messages)
    if LOG_MODE in {"BASIC", "BASIC_PLAIN"} and last_user:
        log("INFO", "conv_user", req_id=req_id, text=last_user)

    ctx_eff = await get_ctx_len_for_model(upstream_model)

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

    if is_passthrough:
        log("INFO", "summary_bypassed", req_id=req_id, reason="passthrough_model")
    elif skip_summary_for_tools:
        log("INFO", "summary_bypassed", req_id=req_id, reason="memory_payload")
    elif not plan.should:
        log("INFO", "summary_bypassed", req_id=req_id, reason=plan.reason, prompt_tok_est=plan.prompt_tok_est, threshold=plan.threshold)

    if (not is_passthrough) and (not skip_summary_for_tools) and plan.should:
        try:
            log(
                "INFO",
                "summary_needed",
                req_id=req_id,
                prompt_tok_est=plan.prompt_tok_est,
                threshold=plan.threshold,
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
                    threshold=plan.threshold,
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
                        summary_text = await summarize_middle(middle, req_id=req_id, summary_model=summary_model)
                        repacked_messages, _middle_used = build_repacked_messages(
                            messages,
                            summary_text=summary_text,
                            head_n=head_n,
                            tail_n=tail_n,
                            pinned_head_n=pinned_head_n,
                        )
                        did_summarize = True
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
                                path = save_cache_entry(SUMMARY_CACHE_DIR, entry)
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
                                path = save_cache_entry(SUMMARY_CACHE_DIR, entry)
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
                summary_text = await summarize_middle(middle, req_id=req_id, summary_model=summary_model)
                repacked_messages, _middle_used = build_repacked_messages(
                    messages,
                    summary_text=summary_text,
                    head_n=plan.head_n,
                    tail_n=plan.tail_n,
                    pinned_head_n=plan.pinned_head_n,
                )
                did_summarize = True

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

    max_tokens_upstream = max(64, int(ctx_eff) - int(prompt_tokens_for_log or 0) - int(SAFETY_MARGIN_TOK))
    requested_out = int(max_tokens_req) if isinstance(max_tokens_req, int) and max_tokens_req > 0 else 900
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
        upstream_url=f"{UPSTREAM_BASE_URL}/v1/chat/completions",
        prompt_tokens=prompt_tokens_for_log,
        **req_summary,
        adjusted_max_tokens=upstream_payload.get("max_tokens"),
        body_json=snip_json(upstream_payload),
    )

    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    client = await http_client()

    if stream:
        async def _iter():
            t0 = time.perf_counter()
            captured = bytearray()

            # Reconstruct full assistant reply from streamed SSE events (OpenAI-compatible chunks).
            sse_buf = ""
            assistant_parts: list[str] = []
            final_usage: dict | None = None
            finish_reason: str | None = None
            upstream_model_seen: str | None = None
            stream_event_count = 0
            r: httpx.Response | None = None
            try:
                async with client.stream("POST", url, json=upstream_payload) as resp:
                    r = resp
                    resp.raise_for_status()
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
                            if not payload_sse or payload_sse == "[DONE]":
                                continue

                            stream_event_count += 1
                            try:
                                obj = json.loads(payload_sse)
                            except Exception:
                                continue

                            if not isinstance(obj, dict):
                                continue

                            if upstream_model_seen is None and isinstance(obj.get("model"), str):
                                upstream_model_seen = obj.get("model")

                            if isinstance(obj.get("usage"), dict):
                                final_usage = obj.get("usage")

                            choices = obj.get("choices")
                            if isinstance(choices, list) and choices:
                                c0 = choices[0] if isinstance(choices[0], dict) else None
                                if isinstance(c0, dict):
                                    delta = c0.get("delta")
                                    if isinstance(delta, dict):
                                        piece = delta.get("content")
                                        if isinstance(piece, str) and piece:
                                            assistant_parts.append(piece)
                                    msg_obj = c0.get("message")
                                    if isinstance(msg_obj, dict):
                                        piece = msg_obj.get("content")
                                        if isinstance(piece, str) and piece:
                                            assistant_parts.append(piece)
                                    fr = c0.get("finish_reason")
                                    if isinstance(fr, str) and fr:
                                        finish_reason = fr

                        yield chunk
            except httpx.HTTPStatusError as e:
                err_text = e.response.text
                log(
                    "ERROR",
                    "upstream_http_error_stream",
                    req_id=req_id,
                    status=e.response.status_code,
                    body=err_text[:500],
                    did_summarize=did_summarize,
                    passthrough=is_passthrough,
                )
                yield (f"data: {json.dumps({'error': {'message': 'Upstream error', 'details': err_text}})}\n\n").encode("utf-8")
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
                    if LOG_MODE == "DEBUG":
                        log(
                            "INFO",
                            "response_stream_reconstructed",
                            req_id=req_id,
                            url=url,
                            upstream_model=(upstream_model_seen or upstream_payload.get("model")),
                            elapsed_ms=elapsed_ms,
                            did_summarize=did_summarize,
                            passthrough=is_passthrough,
                            finish_reason=finish_reason,
                            usage=final_usage,
                            event_count=stream_event_count,
                            assistant_text=full_text,
                        )
                    elif LOG_MODE == "MEDIUM":
                        log(
                            "INFO",
                            "response_stream_reconstructed",
                            req_id=req_id,
                            upstream_model=(upstream_model_seen or upstream_payload.get("model")),
                            elapsed_ms=elapsed_ms,
                            finish_reason=finish_reason,
                            usage=final_usage,
                            event_count=stream_event_count,
                            assistant_text=_snip_obj_active(full_text, LOG_SNIP_CHARS),
                        )
                    else:
                        log(
                            "INFO",
                            "response_stream_reconstructed",
                            req_id=req_id,
                            upstream_model=(upstream_model_seen or upstream_payload.get("model")),
                            elapsed_ms=elapsed_ms,
                            usage=final_usage,
                            finish_reason=finish_reason,
                            assistant_text=_snip_obj_active(full_text, BASIC_SNIP_CHARS),
                        )
                except Exception as _e:
                    log("WARN", "stream_reconstruct_log_failed", req_id=req_id, err=str(_e))

        log("INFO", "upstream_stream_start", req_id=req_id, did_summarize=did_summarize, passthrough=is_passthrough)
        headers = {
            "content-type": "text/event-stream; charset=utf-8",
            "cache-control": "no-cache",
            "connection": "keep-alive",
        }
        return StreamingResponse(_iter(), headers=headers)

    t0 = time.time()
    try:
        r = await client.post(url, json=upstream_payload)
        elapsed_ms = (time.time() - t0) * 1000.0
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
                data=_snip_obj_active(data, LOG_SNIP_CHARS),
            )
        else:
            log(
                "INFO",
                "http_out",
                req_id=req_id,
                status=200,
                elapsed_ms=round(elapsed_ms, 2),
                **resp_summary,
            )
        return JSONResponse(data, status_code=200)
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        log(
            "ERROR",
            "proxy_exception",
            req_id=req_id,
            elapsed_ms=round(elapsed_ms, 2),
            err=str(e),
            did_summarize=did_summarize,
            passthrough=is_passthrough,
        )
        raise e
        return JSONResponse({"error": {"message": "Proxy exception", "details": str(e)}}, status_code=500)
