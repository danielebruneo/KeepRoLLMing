from __future__ import annotations

import json
import os
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
from .rolling_summary import build_repacked_messages, should_summarise, summarize_middle
from .token_counter import TokenCounter
from .upstream import close_http_client, get_ctx_len_for_model, http_client

# ----------------------------
# Token counter
# ----------------------------
TOK = TokenCounter()

# Max chars for logging large payloads (input conversation, summary requests, etc.)
LOG_PAYLOAD_MAX_CHARS = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", "20000000"))

MAX_SSE_BYTES = 8000  # capture only the first N bytes of SSE bodies for logging


def _contains_archived_context(messages: List[Dict[str, Any]]) -> bool:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            content = m.get("content")
            if isinstance(content, str) and "[ARCHIVED_COMPACT_CONTEXT]" in content:
                return True
    return False


def _is_tool_orchestration_payload(payload: Dict[str, Any], messages: List[Dict[str, Any]]) -> bool:
    kind = classify_messages(messages)
    if kind in {"web_search", "memory"}:
        return True
    if isinstance(payload.get("tools"), list) and payload.get("tools"):
        return True
    return any(isinstance(m, dict) and m.get("role") == "tool" for m in messages)


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

    if LOG_MODE == "DEBUG":
        log("INFO", "payload_in_full", req_id=req_id, body_json=snip_json(payload))

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
    max_out = int(max_tokens_req) if isinstance(max_tokens_req, int) and max_tokens_req > 0 else 900

    plan = should_summarise(
        tok=TOK,
        messages=messages,
        ctx_eff=ctx_eff,
        max_out=max_out,
    )

    did_summarize = False
    repacked_messages = messages
    skip_summary_for_tools = _is_tool_orchestration_payload(payload, messages)

    if (not is_passthrough) and (not skip_summary_for_tools) and plan.should:
        # middle are the non-system messages between head and tail chosen by plan
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
            )

            # build the middle list to summarize
            # (we rely on build_repacked_messages to slice consistently and return the middle used)
            # First slice middle using the same logic:
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
            )
            did_summarize = True
            repacked_tok_est = TOK.count_messages(repacked_messages)
            log(
                "INFO",
                "repacked",
                req_id=req_id,
                did_summarize=True,
                repacked_msg_count=len(repacked_messages),
                repacked_tok_est=repacked_tok_est,
                head_n=plan.head_n,
                tail_n=plan.tail_n,
            )
        except Exception as e:
            # Fail-open: passthrough original messages (no summary)
            log("ERROR", "summary_failed_fallback_passthrough", req_id=req_id, err=str(e))
            repacked_messages = messages
            did_summarize = False

    upstream_payload = dict(payload)
    upstream_payload["model"] = upstream_model
    upstream_payload["messages"] = repacked_messages

    req_summary = summarize_request_payload(upstream_payload)
    prompt_tokens_for_log = None
    try:
        prompt_tokens_for_log = TOK.count_messages(repacked_messages)
    except Exception:
        prompt_tokens_for_log = None

    log(
        "INFO",
        "upstream_req_repacked",
        req_id=req_id,
        did_summarize=did_summarize,
        passthrough=is_passthrough,
        upstream_url=f"{UPSTREAM_BASE_URL}/v1/chat/completions",
        prompt_tokens=prompt_tokens_for_log,
        **req_summary,
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
