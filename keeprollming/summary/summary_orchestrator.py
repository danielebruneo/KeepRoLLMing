"""Async summary execution with retry logic."""

import json
import time
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------
# Configuration imports (lazy)
# ---------------------------------------------------------------------
def _get_config():
    """Lazy import of config constants."""
    from .config import SUMMARY_TEMPERATURE, MAX_SUMMARY_BACKEND_ATTEMPTS
    return {
        "SUMMARY_TEMPERATURE": SUMMARY_TEMPERATURE,
        "MAX_SUMMARY_BACKEND_ATTEMPTS": MAX_SUMMARY_BACKEND_ATTEMPTS,
    }


# ---------------------------------------------------------------------
# HTTP client & summary request
# ---------------------------------------------------------------------

async def _request_summary_completion(body: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
    """Make HTTP request to summary backend."""
    from ..config import UPSTREAM_BASE_URL
    from ..upstream import http_client
    
    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    client = await http_client(request_timeout=timeout)
    r = await client.post(url, json=body)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------
# Error handling helpers
# ---------------------------------------------------------------------

def _extract_backend_ctx_error_message(err: Exception) -> str:
    """Extract error message from backend context overflow."""
    resp = getattr(err, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return getattr(resp, "text", "") or str(err)
    return str(err)


def _http_status_from_error(err: Exception) -> int | None:
    """Extract HTTP status code from error."""
    resp = getattr(err, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def _is_context_overflow_error(err: Exception) -> bool:
    """Check if error is a context overflow."""
    txt = _extract_backend_ctx_error_message(err).lower()
    patterns = [
        "available context size",
        "exceeds the available context size",
        "exceed_context_size_error",
        "maximum context length",
        "context length exceeded",
        "context window exceeded",
        "too many tokens",
        "prompt is too long",
        "n_ctx",
    ]
    if any(p in txt for p in patterns):
        return True
    return ("context" in txt and any(k in txt for k in ["exceed", "overflow", "too large", "too long", "limit"]))


def _should_retry_with_reduced_context(err: Exception) -> bool:
    """Check if we should retry with reduced context."""
    status = _http_status_from_error(err)
    if status == 400:
        return True
    if isinstance(status, int) and 500 <= status < 600:
        return True
    txt = _extract_backend_ctx_error_message(err).lower()
    if any(k in txt for k in ["bad request", "server error", "internal server error", "upstream error"]):
        return True
    return False


def _reduced_ctx_for_retry(summary_ctx: int) -> int:
    """Calculate reduced context size for retry."""
    summary_ctx = max(512, int(summary_ctx))
    return max(512, summary_ctx // 2)


# ---------------------------------------------------------------------
# Core summarization logic
# ---------------------------------------------------------------------

async def _summarize_middle_core(
    middle: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "english",
    request_timeout: float = 120.0,
) -> str:
    """Core summarization logic for a chunk of messages."""
    from ..logger import log, snip_json
    from .chunking_strategy import render_messages_for_summary
    from .prompt_engine import get_summary_system_prompt, render_summary_prompt
    
    transcript = render_messages_for_summary(middle)

    # If we have a custom prompt provided as the actual text, use it directly
    if prompt_type and not isinstance(prompt_type, str):
        sys = get_summary_system_prompt()
        user = prompt_type  # treat it as direct prompt text
    else:
        sys = get_summary_system_prompt(prompt_type=prompt_type)
        user = render_summary_prompt(transcript, prompt_type=prompt_type, lang_hint=lang_hint)

    config = _get_config()
    body = {
        "model": summary_model,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        "temperature": config["SUMMARY_TEMPERATURE"],
        "max_tokens": 512,  # SUMMARY_MAX_TOKENS
        "stream": False,
    }
    
    log("INFO", "summary_req", req_id=req_id, summary_model=summary_model, summary_prompt_type=(prompt_type or "curated"), middle_count=len(middle), transcript_chars=len(transcript), body_json=snip_json(body))
    
    t0 = time.time()
    data = await _request_summary_completion(body, timeout=request_timeout)
    elapsed_ms = (time.time() - t0) * 1000.0
    
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    
    try:
        summary = data["choices"][0]["message"]["content"]
    except Exception:
        summary = ""
    
    summary = _sanitize_summary_text(summary, fallback="(Context not available (compacted).)") or "(Context not available (compacted).)"
    
    log("INFO", "summary_reply", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), usage=data.get("usage"), summary_chars=len(summary), summary_snip=summary, raw_json=snip_json(data))
    return summary


# ---------------------------------------------------------------------
# Main summarization entry points with retry logic
# ---------------------------------------------------------------------

async def summarize_middle(
    middle: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "english",
    _attempt: int = 0,
) -> str:
    """Main entry point for summarization with retry logic.

    This is the main async function that handles chunking and retries automatically.
    """
    config = _get_config()
    
    if _attempt >= config["MAX_SUMMARY_BACKEND_ATTEMPTS"]:
        from ..logger import log
        log("ERROR", "summary_retry_exhausted", req_id=req_id, summary_model=summary_model, attempts=_attempt, max_attempts=config["MAX_SUMMARY_BACKEND_ATTEMPTS"], middle_count=len(middle))
        raise RuntimeError(f"summary retry exhausted after {config['MAX_SUMMARY_BACKEND_ATTEMPTS']} attempts")

    # Check if we need to pre-chunk
    from .chunking_strategy import _should_prechunk_summary_call_async, _chunk_messages_for_summary, _normalize_retry_chunks
    
    should_prechunk, est_tokens, threshold = await _should_prechunk_summary_call_async(
        middle,
        summary_model=summary_model,
        prompt_type=prompt_type,
        lang_hint=lang_hint,
        incremental_existing_summary=None,
    )
    
    if should_prechunk:
        from ..upstream import get_ctx_len_for_model
        summary_ctx = await get_ctx_len_for_model(summary_model)
        chunks = _chunk_messages_for_summary(middle, prompt_type=prompt_type, lang_hint=lang_hint, summary_model_ctx=summary_ctx)
        chunks, normalization_reason = _normalize_retry_chunks(middle, chunks)
        
        from ..logger import log
        log("WARN", "summary_preflight_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, est_tokens=est_tokens, threshold=threshold, normalization=normalization_reason)
        
        if normalization_reason == "forced_split_no_progress":
            log("WARN", "summary_preflight_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
        if normalization_reason == "single_chunk_no_progress":
            log("ERROR", "summary_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, est_tokens=est_tokens, threshold=threshold)
            raise RuntimeError("summary preflight produced no-progress single chunk")
        
        # Summarize each chunk and merge
        partials: List[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            partials.append(await summarize_middle(chunk, f"{req_id}-c{idx}", summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1))
        
        merge_messages = [{"role": "user", "content": f"[PARTIAL SUMMARY {i}]\n{s}"} for i, s in enumerate(partials, start=1)]
        if len(merge_messages) == 1:
            return partials[0]
        return await summarize_middle(merge_messages, req_id=f"{req_id}-merge", summary_model=summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1)

    # No pre-chunking needed, call core directly
    try:
        return await _summarize_middle_core(middle, req_id, summary_model, prompt_type=prompt_type, lang_hint=lang_hint)
    except Exception as err:
        from ..upstream import get_ctx_len_for_model
        summary_ctx = await get_ctx_len_for_model(summary_model)
        
        retry_reason = "overflow" if _is_context_overflow_error(err) else "http_retry" if _should_retry_with_reduced_context(err) else "fatal"
        
        if retry_reason == "overflow":
            from .chunking_strategy import _chunk_messages_for_summary, _normalize_retry_chunks
            chunks = _chunk_messages_for_summary(middle, prompt_type=prompt_type, lang_hint=lang_hint, summary_model_ctx=summary_ctx)
            chunks, normalization_reason = _normalize_retry_chunks(middle, chunks)
            
            from ..logger import log
            log("WARN", "summary_overflow_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, normalization=normalization_reason)
            
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_overflow_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        
        elif retry_reason == "http_retry":
            reduced_ctx = _reduced_ctx_for_retry(summary_ctx)
            from .chunking_strategy import _chunk_messages_for_summary, _normalize_retry_chunks
            chunks = _chunk_messages_for_summary(middle, prompt_type=prompt_type, lang_hint=lang_hint, summary_model_ctx=reduced_ctx)
            chunks, normalization_reason = _normalize_retry_chunks(middle, chunks)
            
            from ..logger import log
            log("WARN", "summary_http_retry_reduced_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, status=_http_status_from_error(err), reduced_ctx=reduced_ctx, err=_extract_backend_ctx_error_message(err), normalization=normalization_reason)
            
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_http_retry_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        
        else:
            raise
        
        # Retry with chunks
        partials: List[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            partials.append(await summarize_middle(chunk, f"{req_id}-c{idx}", summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1))
        
        merge_messages = [{"role": "user", "content": f"[PARTIAL SUMMARY {i}]\n{s}"} for i, s in enumerate(partials, start=1)]
        if len(merge_messages) == 1:
            return partials[0]
        return await summarize_middle(merge_messages, req_id=f"{req_id}-merge", summary_model=summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1)


# ---------------------------------------------------------------------
# Incremental summarization
# ---------------------------------------------------------------------

async def _summarize_incremental_core(
    existing_summary: str,
    new_messages: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "english",
) -> str:
    """Core logic for incremental summary update."""
    from ..logger import log, snip_json
    from .chunking_strategy import render_messages_for_summary
    
    # If we have a custom prompt provided as the actual text, use it directly
    if prompt_type and not isinstance(prompt_type, str):
        sys = "You are an assistant that updates a context summary for another model. Do not invent anything. Keep the result compact and faithful."
        user = render_messages_for_summary(new_messages)  # Simplified for incremental
    else:
        sys = "You are an assistant that updates a context summary for another model. Do not invent anything. Keep the result compact and faithful."
        from .prompt_engine import render_incremental_summary_prompt
        user = render_incremental_summary_prompt(existing_summary, new_messages, lang_hint=lang_hint)

    config = _get_config()
    body = {
        "model": summary_model,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        "temperature": config["SUMMARY_TEMPERATURE"],
        "max_tokens": 512,  # SUMMARY_MAX_TOKENS
        "stream": False,
    }

    log("INFO", "summary_req", req_id=req_id, summary_model=summary_model, summary_prompt_type="incremental", middle_count=len(new_messages), transcript_chars=len(user), body_json=snip_json(body))
    
    t0 = time.time()
    data = await _request_summary_completion(body)
    elapsed_ms = (time.time() - t0) * 1000.0
    
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    
    try:
        summary = data["choices"][0]["message"]["content"]
    except Exception:
        summary = ""
    
    summary = _sanitize_summary_text(summary, fallback=existing_summary.strip() or "(Context not available (compacted).)") or existing_summary.strip() or "(Context not available (compacted).)"
    
    log("INFO", "summary_reply", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), usage=data.get("usage"), summary_chars=len(summary), summary_snip=summary, raw_json=snip_json(data))
    return summary


async def summarize_incremental(
    existing_summary: str,
    new_messages: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "english",
    _attempt: int = 0,
) -> str:
    """Incremental summary update with retry logic."""
    config = _get_config()
    
    if _attempt >= config["MAX_SUMMARY_BACKEND_ATTEMPTS"]:
        from ..logger import log
        log("ERROR", "summary_incremental_retry_exhausted", req_id=req_id, summary_model=summary_model, attempts=_attempt, max_attempts=config["MAX_SUMMARY_BACKEND_ATTEMPTS"], new_messages_count=len(new_messages))
        raise RuntimeError(f"incremental summary retry exhausted after {config['MAX_SUMMARY_BACKEND_ATTEMPTS']} attempts")

    # Check if we need to pre-chunk
    from .chunking_strategy import _should_prechunk_summary_call_async, _chunk_messages_for_summary, _normalize_retry_chunks
    
    should_prechunk, est_tokens, threshold = await _should_prechunk_summary_call_async(
        new_messages,
        summary_model=summary_model,
        prompt_type=prompt_type,
        lang_hint=lang_hint,
        incremental_existing_summary=existing_summary,
    )
    
    if should_prechunk:
        from ..upstream import get_ctx_len_for_model
        summary_ctx = await get_ctx_len_for_model(summary_model)
        chunks = _chunk_messages_for_summary(new_messages, prompt_type=None, lang_hint=lang_hint, summary_model_ctx=summary_ctx, incremental_existing_summary=existing_summary)
        chunks, normalization_reason = _normalize_retry_chunks(new_messages, chunks)
        
        from ..logger import log
        log("WARN", "summary_incremental_preflight_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, est_tokens=est_tokens, threshold=threshold, normalization=normalization_reason)
        
        if normalization_reason == "forced_split_no_progress":
            log("WARN", "summary_incremental_preflight_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
        if normalization_reason == "single_chunk_no_progress":
            log("ERROR", "summary_incremental_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, est_tokens=est_tokens, threshold=threshold)
            raise RuntimeError("incremental summary preflight produced no-progress single chunk")
        
        current = existing_summary
        for idx, chunk in enumerate(chunks, start=1):
            current = await summarize_incremental(current, chunk, f"{req_id}-c{idx}", summary_model, lang_hint=lang_hint, _attempt=_attempt + 1)
        return current

    # No pre-chunking needed
    try:
        return await _summarize_incremental_core(existing_summary, new_messages, req_id, summary_model, prompt_type=prompt_type, lang_hint=lang_hint)
    except Exception as err:
        from ..upstream import get_ctx_len_for_model
        summary_ctx = await get_ctx_len_for_model(summary_model)
        
        retry_reason = "overflow" if _is_context_overflow_error(err) else "http_retry" if _should_retry_with_reduced_context(err) else "fatal"
        
        if retry_reason == "overflow":
            from .chunking_strategy import _chunk_messages_for_summary, _normalize_retry_chunks
            chunks = _chunk_messages_for_summary(new_messages, prompt_type=None, lang_hint=lang_hint, summary_model_ctx=summary_ctx, incremental_existing_summary=existing_summary)
            chunks, normalization_reason = _normalize_retry_chunks(new_messages, chunks)
            
            from ..logger import log
            log("WARN", "summary_incremental_overflow_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, normalization=normalization_reason)
            
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_incremental_overflow_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_incremental_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        
        elif retry_reason == "http_retry":
            reduced_ctx = _reduced_ctx_for_retry(summary_ctx)
            from .chunking_strategy import _chunk_messages_for_summary, _normalize_retry_chunks
            chunks = _chunk_messages_for_summary(new_messages, prompt_type=None, lang_hint=lang_hint, summary_model_ctx=reduced_ctx, incremental_existing_summary=existing_summary)
            chunks, normalization_reason = _normalize_retry_chunks(new_messages, chunks)
            
            from ..logger import log
            log("WARN", "summary_incremental_http_retry_reduced_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, status=_http_status_from_error(err), reduced_ctx=reduced_ctx, err=_extract_backend_ctx_error_message(err), normalization=normalization_reason)
            
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_incremental_http_retry_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_incremental_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        
        else:
            raise
        
        # Retry with chunks
        current = existing_summary
        for idx, chunk in enumerate(chunks, start=1):
            current = await summarize_incremental(current, chunk, f"{req_id}-c{idx}", summary_model, lang_hint=lang_hint, _attempt=_attempt + 1)
        return current


# ---------------------------------------------------------------------
# Summary text utilities
# ---------------------------------------------------------------------

def _sanitize_summary_text(text: str, *, fallback: str = "") -> str:
    """Sanitize summary text by removing artifacts and normalizing."""
    import re

    out = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not out:
        return (fallback or "").strip()

    # Remove common artifacts - these are prompt echo markers, keep only content
    out = re.sub(r"=== NEW MESSAGES START ===.*?=== NEW MESSAGES END ===", "", out, flags=re.S)
    out = out.replace("=== EXISTING SUMMARY START ===", "")
    out = out.replace("=== EXISTING SUMMARY END ===", "")
    out = out.replace("[ARCHIVED_COMPACT_CONTEXT]", "")
    out = out.replace("[/ARCHIVED_COMPACT_CONTEXT]", "")
    out = re.sub(r"^\s*\[/?EXTRACTION_SUMMARY_(?:START|END)\]\s*$", "", out, flags=re.M)
    out = re.sub(r"^\s*EXTRACTION_SUMMARY_(?:START|END)\s*$", "", out, flags=re.M)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    return out or (fallback or "").strip()


def is_summary_placeholder(text: str) -> bool:
    """Check if text is a placeholder indicating summary unavailable."""
    s = (text or "").strip().lower()
    if not s:
        return True
    placeholders = [
        "(contesto compattato non disponibile.)",
        "context unavailable",
        "summary unavailable",
        "[placeholder]",
    ]
    return s in placeholders


def is_summary_cacheable(text: str) -> bool:
    """Check if summary text is cacheable."""
    s = _sanitize_summary_text(text or "")
    if is_summary_placeholder(s):
        return False
    # Avoid caching empty / near-empty accidental outputs
    if len(s) < 8:
        return False
    return True
