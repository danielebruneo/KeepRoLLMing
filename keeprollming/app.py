from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .config import (
    MAIN_MODEL,
    SAFETY_MARGIN_TOK,
    SUMMARY_CACHE_DIR,
    SUMMARY_CACHE_ENABLED,
    SUMMARY_MIN_RAW_TAIL,
    SUMMARY_MODE,
    SUMMARY_MODEL,
    UPSTREAM_BASE_URL,
    resolve_profile_and_models,
)
from .logger import (
    BASIC_SNIP_CHARS,
    LOG_MODE,
    LOG_SNIP_CHARS,
    extract_last_user_text,
    log,
    log_streaming_response,
    _snip_obj_active,
    snip_json,
)
from .rolling_summary import (
    build_checkpoint_repacked_messages,
    build_repacked_messages,
    should_summarise,
    split_messages,
    summarize_incremental,
    summarize_middle,
)
from .summary_cache import (
    find_best_checkpoint,
    load_cache_entries,
    resolve_cache_scope,
    save_cache_entry,
)
from .token_counter import TokenCounter
from .upstream import close_http_client, get_ctx_len_for_model, http_client

TOK = TokenCounter()
LOG_PAYLOAD_MAX_CHARS = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", "20000000"))
MAX_SSE_BYTES = 8000


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await close_http_client()


app = FastAPI(lifespan=lifespan)


def _headers_dict(req: Request) -> Dict[str, str]:
    return {k.lower(): v for k, v in req.headers.items()}


def _flatten_message_content(msg: Dict[str, Any]) -> Dict[str, Any]:
    content = msg.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                txt = item.get("text")
                if isinstance(txt, str) and txt:
                    parts.append(txt)
        out = dict(msg)
        out["content"] = "\n".join(parts).strip()
        return out
    return msg


def _normalize_messages_for_upstream(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_flatten_message_content(m) for m in messages]


def _checkpoint_tail_fit(messages: List[Dict[str, Any]], summary_text: str, covered_count: int, threshold: int) -> Tuple[bool, List[Dict[str, Any]]]:
    _, non_system = split_messages(messages)
    tail = non_system[covered_count:]
    repacked = build_checkpoint_repacked_messages(messages, summary_text=summary_text, covered_count=covered_count, tail_messages=tail)
    return TOK.count_messages(repacked) <= threshold, repacked


async def _prepare_messages_with_cache(
    *,
    req: Request,
    req_id: str,
    messages: List[Dict[str, Any]],
    summary_model: str,
    plan,
) -> Tuple[List[Dict[str, Any]], bool, bool]:
    headers = _headers_dict(req)
    sys_msg, non_system = split_messages(messages)
    if len(non_system) < 3 or not SUMMARY_CACHE_ENABLED:
        return messages, False, False

    scope = resolve_cache_scope(headers, non_system)
    entries = load_cache_entries(SUMMARY_CACHE_DIR, scope)
    best = find_best_checkpoint(entries, non_system)
    log("INFO", "summary_cache_lookup", req_id=req_id, provider=scope.provider, scope_key=scope.scope_key, candidates=len(entries), hit=bool(best), hit_end_idx=(best.end_idx if best else None))

    threshold = plan.threshold
    if best is not None:
        covered_count = best.end_idx + 1
        fits, repacked = _checkpoint_tail_fit(messages, best.summary_text, covered_count, threshold)
        if fits:
            log("INFO", "summary_cache_hit", req_id=req_id, end_idx=best.end_idx, tail_count=max(0, len(non_system) - covered_count))
            return repacked, True, True

    keep_tail = min(max(1, SUMMARY_MIN_RAW_TAIL), max(1, len(non_system) - 1))
    covered_count = max(0, len(non_system) - keep_tail)
    if covered_count <= 0:
        return messages, False, False

    summary_text: str
    source_mode = "full_regen"
    if best is not None and best.end_idx + 1 < covered_count:
        new_messages = non_system[best.end_idx + 1:covered_count]
        summary_text = await summarize_incremental(best.summary_text, new_messages, req_id=req_id, summary_model=summary_model)
        source_mode = "cache_append_consolidated"
        log("INFO", "summary_cache_rebuild", req_id=req_id, from_end_idx=best.end_idx, to_end_idx=covered_count - 1, new_count=len(new_messages))
    else:
        summary_text = await summarize_middle(non_system[:covered_count], req_id=req_id, summary_model=summary_model)
        log("INFO", "summary_cache_miss", req_id=req_id, covered_count=covered_count)

    repacked = build_checkpoint_repacked_messages(messages, summary_text=summary_text, covered_count=covered_count, tail_messages=non_system[covered_count:])
    token_est = TOK.count_text(summary_text)
    cache_path = save_cache_entry(
        SUMMARY_CACHE_DIR,
        scope,
        non_system,
        end_idx=covered_count - 1,
        summary_text=summary_text,
        summary_model=summary_model,
        token_estimate=token_est,
        source_mode=source_mode,
    )
    log("INFO", "summary_cache_save", req_id=req_id, path=cache_path, end_idx=covered_count - 1, source_mode=source_mode)
    return repacked, True, False


@app.post("/v1/chat/completions")
async def chat_completions(req: Request) -> Response:
    req_id = os.urandom(6).hex()
    payload = await req.json()

    if LOG_MODE == "DEBUG":
        log("INFO", "payload_in_full", req_id=req_id, body_json=snip_json(payload))

    client_model = payload.get("model", MAIN_MODEL)
    profile, upstream_model, summary_model, is_passthrough = resolve_profile_and_models(client_model)

    messages = payload.get("messages")
    if not isinstance(messages, list):
        return JSONResponse({"error": {"message": "Invalid payload: messages must be a list"}}, status_code=400)

    stream = bool(payload.get("stream", False))
    last_user = extract_last_user_text(messages)
    if LOG_MODE == "BASIC" and last_user:
        log("INFO", "conv_user", req_id=req_id, text=last_user)

    ctx_eff = await get_ctx_len_for_model(upstream_model)
    max_tokens_req = payload.get("max_tokens")
    max_out = int(max_tokens_req) if isinstance(max_tokens_req, int) and max_tokens_req > 0 else 900

    plan = should_summarise(tok=TOK, messages=messages, ctx_eff=ctx_eff, max_out=max_out)
    did_summarize = False
    used_cache = False
    repacked_messages = messages

    if (not is_passthrough) and plan.should:
        try:
            log("INFO", "summary_needed", req_id=req_id, prompt_tok_est=plan.prompt_tok_est, threshold=plan.threshold, head_n=plan.head_n, tail_n=plan.tail_n, middle_count=plan.middle_count, summary_model=summary_model, repacked_tok_est=plan.repacked_tok_est)
            if SUMMARY_MODE == "cache_append":
                repacked_messages, did_summarize, used_cache = await _prepare_messages_with_cache(req=req, req_id=req_id, messages=messages, summary_model=summary_model, plan=plan)
            else:
                _, non_system = split_messages(messages)
                middle = non_system[plan.head_n: len(non_system) - plan.tail_n] if (plan.head_n + plan.tail_n) < len(non_system) else []
                summary_text = await summarize_middle(middle, req_id=req_id, summary_model=summary_model)
                repacked_messages, _middle_used = build_repacked_messages(messages, summary_text=summary_text, head_n=plan.head_n, tail_n=plan.tail_n)
                did_summarize = True
            repacked_tok_est = TOK.count_messages(repacked_messages)
            log("INFO", "repacked", req_id=req_id, did_summarize=did_summarize, used_cache=used_cache, repacked_msg_count=len(repacked_messages), repacked_tok_est=repacked_tok_est)
        except Exception as e:
            log("ERROR", "summary_failed_fallback_passthrough", req_id=req_id, err=str(e))
            repacked_messages = messages
            did_summarize = False

    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model
    upstream_payload["messages"] = _normalize_messages_for_upstream(repacked_messages)

    log("INFO", "upstream_req_repacked", req_id=req_id, did_summarize=did_summarize, used_cache=used_cache, passthrough=is_passthrough, upstream_url=f"{UPSTREAM_BASE_URL}/v1/chat/completions", body_json=snip_json(upstream_payload))

    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    client = await http_client()

    if stream:
        async def _iter():
            t0 = time.perf_counter()
            captured = bytearray()
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
                        if LOG_MODE == "DEBUG":
                            try:
                                await log_streaming_response(resp, chunk, elapsed_ms=None)
                            except Exception:
                                pass
                        if len(captured) < MAX_SSE_BYTES:
                            take = min(len(chunk), MAX_SSE_BYTES - len(captured))
                            captured.extend(chunk[:take])
                        try:
                            sse_buf += chunk.decode("utf-8", errors="replace")
                        except Exception:
                            pass
                        while True:
                            sep = sse_buf.find("\n\n")
                            if sep == -1:
                                break
                            block = sse_buf[:sep]
                            sse_buf = sse_buf[sep + 2:]
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
                                    fr = c0.get("finish_reason")
                                    if isinstance(fr, str) and fr:
                                        finish_reason = fr
                        yield chunk
            except httpx.HTTPStatusError as e:
                err_text = e.response.text
                log("ERROR", "upstream_http_error_stream", req_id=req_id, status=e.response.status_code, body=err_text[:500], did_summarize=did_summarize, passthrough=is_passthrough)
                yield (f"data: {json.dumps({'error': {'message': 'Upstream error', 'details': err_text}})}\n\n").encode("utf-8")
                yield b"data: [DONE]\n\n"
            except Exception as e:
                log("ERROR", "upstream_stream_exception", req_id=req_id, err=str(e), did_summarize=did_summarize, passthrough=is_passthrough)
                yield (f"data: {json.dumps({'error': {'message': 'Upstream stream exception', 'details': str(e)}})}\n\n").encode("utf-8")
                yield b"data: [DONE]\n\n"
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                if r is not None:
                    try:
                        await log_streaming_response(r, bytes(captured), elapsed_ms=elapsed_ms)
                    except Exception as _e:
                        log("WARN", "stream_log_failed", req_id=req_id, err=str(_e))
                try:
                    full_text = "".join(assistant_parts).strip()
                    if LOG_MODE == "DEBUG":
                        log("INFO", "response_stream_reconstructed", req_id=req_id, url=url, upstream_model=(upstream_model_seen or upstream_payload.get("model")), elapsed_ms=elapsed_ms, did_summarize=did_summarize, passthrough=is_passthrough, finish_reason=finish_reason, usage=final_usage, event_count=stream_event_count, assistant_text=full_text)
                    elif LOG_MODE == "MEDIUM":
                        log("INFO", "response_stream_reconstructed", req_id=req_id, upstream_model=(upstream_model_seen or upstream_payload.get("model")), elapsed_ms=elapsed_ms, finish_reason=finish_reason, usage=final_usage, event_count=stream_event_count, assistant_text=_snip_obj_active(full_text, LOG_SNIP_CHARS))
                    else:
                        log("INFO", "conv_assistant", req_id=req_id, text=_snip_obj_active(full_text, BASIC_SNIP_CHARS), finish_reason=finish_reason)
                except Exception as _e:
                    log("WARN", "stream_reconstruct_log_failed", req_id=req_id, err=str(_e))

        log("INFO", "upstream_stream_start", req_id=req_id, did_summarize=did_summarize, passthrough=is_passthrough)
        headers = {"content-type": "text/event-stream; charset=utf-8", "cache-control": "no-cache", "connection": "keep-alive"}
        return StreamingResponse(_iter(), headers=headers)

    t0 = time.time()
    try:
        r = await client.post(url, json=upstream_payload)
        elapsed_ms = (time.time() - t0) * 1000.0
        if r.status_code >= 400:
            log("ERROR", "upstream_http_error", req_id=req_id, status=r.status_code, body=r.text[:800], did_summarize=did_summarize, passthrough=is_passthrough)
            return JSONResponse({"error": {"message": "Upstream error", "details": r.text}}, status_code=r.status_code)
        data = r.json()
        if LOG_MODE == "DEBUG":
            log("INFO", "http_out", req_id=req_id, status=200, elapsed_ms=round(elapsed_ms, 2), did_summarize=did_summarize, passthrough=is_passthrough, usage=data.get("usage"), data=data)
        elif LOG_MODE == "MEDIUM":
            log("INFO", "http_out", req_id=req_id, status=200, elapsed_ms=round(elapsed_ms, 2), usage=data.get("usage"), data=_snip_obj_active(data, LOG_SNIP_CHARS))
        else:
            assistant_text = None
            try:
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                        assistant_text = msg.get("content")
            except Exception:
                pass
            if assistant_text:
                log("INFO", "conv_assistant", req_id=req_id, text=_snip_obj_active(assistant_text, BASIC_SNIP_CHARS))
        return JSONResponse(data, status_code=200)
    except Exception as e:
        elapsed_ms = (time.time() - t0) * 1000.0
        log("ERROR", "proxy_exception", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), err=str(e), did_summarize=did_summarize, passthrough=is_passthrough)
        raise e
