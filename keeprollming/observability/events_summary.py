"""Summary event emission helpers for O7 migration.

Provides ``emit_summary_event()`` — a thin wrapper that creates a
``RuntimeEvent`` with domain="execution", component="summary", and emits it
through an optional ``event_dispatcher``.

When no dispatcher is available the call falls back to ``log()``
for the configurable PLAIN and JSON projectors.

Event type mapping (legacy log() → RuntimeEvent.type):

    summary_plan                            → execution.summary.plan
    summary_req                             → execution.summary.request
    summary_reply                           → execution.summary.reply
    summary_retry_exhausted                 → execution.summary.retry_exhausted
    summary_incremental_retry_exhausted     → execution.summary.incremental_retry_exhausted
    summary_preflight_chunking              → execution.summary.preflight_chunking
    summary_incremental_preflight_chunking  → execution.summary.incremental_preflight_chunking
    summary_preflight_forced_split          → execution.summary.preflight_forced_split
    summary_incremental_preflight_forced_split→ execution.summary.incremental_preflight_forced_split
    summary_no_progress_abort               → execution.summary.no_progress_abort
    summary_incremental_no_progress_abort   → execution.summary.incremental_no_progress_abort
    summary_overflow_chunking               → execution.summary.overflow_chunking
    summary_incremental_overflow_chunking   → execution.summary.incremental_overflow_chunking
    summary_overflow_forced_split           → execution.summary.overflow_forced_split
    summary_incremental_overflow_forced_split→ execution.summary.incremental_overflow_forced_split
    summary_http_retry_reduced_chunking     → execution.summary.http_retry_chunking
    summary_incremental_http_retry_reduced_chunking→ execution.summary.incremental_http_retry_chunking
    summary_http_retry_forced_split         → execution.summary.http_retry_forced_split
    summary_incremental_http_retry_forced_split→ execution.summary.incremental_http_retry_forced_split

All events use level="INFO" by default unless otherwise noted.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .events import EventSource, RuntimeEvent


def emit_summary_event(
    req_id: str,
    event_type: str,
    level: str = "INFO",
    dispatcher: Optional[Any] = None,
    **data: Any,
) -> Optional[RuntimeEvent]:
    """Emit a summary RuntimeEvent through an optional dispatcher.

    Parameters
    ----------
    req_id : str
        Request correlator.
    event_type : str
        Full hierarchical event type (e.g. "execution.summary.request").
    level : str
        Log level: DEBUG, INFO, WARN, ERROR. Default "INFO".
    dispatcher : EventDispatcher | None
        Optional dispatcher. When None falls back to log().
    **data : Any
        Event payload fields.

    Returns
    -------
    RuntimeEvent | None
        The event that was created, or None if no dispatcher available.
    """
    source = EventSource(domain="execution", component="summary")
    event = RuntimeEvent(
        type=event_type,
        timestamp_ns=time.time_ns(),
        source=source,
        data=data,
        req_id=req_id,
        level=level,
    )
    if dispatcher is not None:
        emit_fn = getattr(dispatcher, "emit", None)
        if emit_fn is not None:
            emit_fn(event)
    # FIX-D072: Fallback to log() removed. When dispatcher is None, the call
    # is a no-op rather than routing through legacy log(). This eliminates
    # the bypass pattern that printed JSON directly to stdout outside
    # Projector control (I-D072-01, D-072 §6).
    return event


# ── Convenience wrappers (mirror legacy log() call signatures) ──────────


def emit_plan(
    should: bool = True, reason: str = "", threshold: int = 0,
    prompt_tok_est: int = 0, head_n: int = 0, tail_n: int = 0,
    middle_count: int = 0, repacked_tok_est: int = 0,
    pinned_head_n: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event("", "execution.summary.plan",
                         should=should, reason=reason or None,
                         threshold=threshold or None,
                         prompt_tok_est=prompt_tok_est or None,
                         head_n=head_n or None, tail_n=tail_n or None,
                         middle_count=middle_count or None,
                         repacked_tok_est=repacked_tok_est or None,
                         pinned_head_n=pinned_head_n or None,
                         dispatcher=dispatcher)


def emit_request(
    req_id: str, summary_model: str = "", summary_prompt_type: str = "curated",
    middle_count: int = 0, transcript_chars: int = 0, body_json: Any = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.request",
                         summary_model=summary_model or None,
                         summary_prompt_type=summary_prompt_type or None,
                         middle_count=middle_count or None,
                         transcript_chars=transcript_chars or None,
                         body_json=body_json,
                         dispatcher=dispatcher)


def emit_reply(
    req_id: str, elapsed_ms: float = 0.0, usage: Any = None,
    summary_chars: int = 0, summary_snip: str = "", raw_json: Any = None,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.reply",
                         elapsed_ms=elapsed_ms or None,
                         usage=usage,
                         summary_chars=summary_chars or None,
                         summary_snip=summary_snip or None,
                         raw_json=raw_json,
                         dispatcher=dispatcher)


def emit_retry_exhausted(
    req_id: str, summary_model: str = "", attempts: int = 0,
    max_attempts: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.retry_exhausted",
                         level="ERROR",
                         summary_model=summary_model or None,
                         attempts=attempts or None,
                         max_attempts=max_attempts or None,
                         dispatcher=dispatcher)


def emit_incremental_retry_exhausted(
    req_id: str, summary_model: str = "", attempts: int = 0,
    max_attempts: int = 0, new_messages_count: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_retry_exhausted",
                         level="ERROR",
                         summary_model=summary_model or None,
                         attempts=attempts or None,
                         max_attempts=max_attempts or None,
                         new_messages_count=new_messages_count or None,
                         dispatcher=dispatcher)


def emit_preflight_chunking(
    req_id: str, chunks: int = 0, summary_model: str = "",
    est_tokens: int = 0, threshold: int = 0, normalization: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.preflight_chunking",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         est_tokens=est_tokens or None,
                         threshold=threshold or None,
                         normalization=normalization or None,
                         dispatcher=dispatcher)


def emit_incremental_preflight_chunking(
    req_id: str, chunks: int = 0, summary_model: str = "",
    est_tokens: int = 0, threshold: int = 0, normalization: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_preflight_chunking",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         est_tokens=est_tokens or None,
                         threshold=threshold or None,
                         normalization=normalization or None,
                         dispatcher=dispatcher)


def emit_preflight_forced_split(
    req_id: str, chunks: int = 0, summary_model: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.preflight_forced_split",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         dispatcher=dispatcher)


def emit_incremental_preflight_forced_split(
    req_id: str, chunks: int = 0, summary_model: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_preflight_forced_split",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         dispatcher=dispatcher)


def emit_no_progress_abort(
    req_id: str, summary_model: str = "", attempts: int = 0,
    est_tokens: int = 0, threshold: int = 0, err: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.no_progress_abort",
                         level="ERROR",
                         summary_model=summary_model or None,
                         attempts=attempts or None,
                         est_tokens=est_tokens or None,
                         threshold=threshold or None,
                         err=err or None,
                         dispatcher=dispatcher)


def emit_incremental_no_progress_abort(
    req_id: str, summary_model: str = "", attempts: int = 0,
    est_tokens: int = 0, threshold: int = 0, err: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_no_progress_abort",
                         level="ERROR",
                         summary_model=summary_model or None,
                         attempts=attempts or None,
                         est_tokens=est_tokens or None,
                         threshold=threshold or None,
                         err=err or None,
                         dispatcher=dispatcher)


def emit_overflow_chunking(
    req_id: str, chunks: int = 0, summary_model: str = "",
    normalization: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.overflow_chunking",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         normalization=normalization or None,
                         dispatcher=dispatcher)


def emit_incremental_overflow_chunking(
    req_id: str, chunks: int = 0, summary_model: str = "",
    normalization: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_overflow_chunking",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         normalization=normalization or None,
                         dispatcher=dispatcher)


def emit_overflow_forced_split(
    req_id: str, chunks: int = 0, summary_model: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.overflow_forced_split",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         dispatcher=dispatcher)


def emit_incremental_overflow_forced_split(
    req_id: str, chunks: int = 0, summary_model: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_overflow_forced_split",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         dispatcher=dispatcher)


def emit_http_retry_chunking(
    req_id: str, chunks: int = 0, summary_model: str = "",
    status: int = 0, reduced_ctx: int = 0, err: str = "",
    normalization: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.http_retry_chunking",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         status=status or None,
                         reduced_ctx=reduced_ctx or None,
                         err=err or None,
                         normalization=normalization or None,
                         dispatcher=dispatcher)


def emit_incremental_http_retry_chunking(
    req_id: str, chunks: int = 0, summary_model: str = "",
    status: int = 0, reduced_ctx: int = 0, err: str = "",
    normalization: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_http_retry_chunking",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         status=status or None,
                         reduced_ctx=reduced_ctx or None,
                         err=err or None,
                         normalization=normalization or None,
                         dispatcher=dispatcher)


def emit_http_retry_forced_split(
    req_id: str, chunks: int = 0, summary_model: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.http_retry_forced_split",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         dispatcher=dispatcher)


def emit_incremental_http_retry_forced_split(
    req_id: str, chunks: int = 0, summary_model: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_http_retry_forced_split",
                         level="WARN",
                         chunks=chunks or None,
                         summary_model=summary_model or None,
                         dispatcher=dispatcher)


# ── O8 wrappers for summarization.py log() migration ──────────────────────


def emit_summary_needed(
    req_id: str, prompt_tok_est: int = 0, threshold: int = 0,
    head_n: int = 0, tail_n: int = 0, middle_count: int = 0,
    summary_model: str = "", repacked_tok_est: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary_needed",
                         prompt_tok_est=prompt_tok_est or None,
                         threshold=threshold or None,
                         head_n=head_n or None, tail_n=tail_n or None,
                         middle_count=middle_count or None,
                         summary_model=summary_model or None,
                         repacked_tok_est=repacked_tok_est or None,
                         dispatcher=dispatcher)


def emit_cache_hit_used(
    req_id: str, cache_entry: Any = None, messages_count: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.cache_hit_used",
                         cache_entry=cache_entry,
                         messages_count=messages_count or None,
                         dispatcher=dispatcher)


def emit_incremental_summary_called(
    req_id: str, messages_count: int = 0,
    summary_model: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.incremental_summary_called",
                         messages_count=messages_count or None,
                         summary_model=summary_model or None,
                         dispatcher=dispatcher)


def emit_repacked(
    req_id: str, did_summarize: bool = False,
    repacked_msg_count: int = 0, head_n: int = 0, tail_n: int = 0,
    pinned_head_n: int = 0,
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.repacked",
                         did_summarize=did_summarize,
                         repacked_msg_count=repacked_msg_count or None,
                         head_n=head_n or None, tail_n=tail_n or None,
                         pinned_head_n=pinned_head_n or None,
                         dispatcher=dispatcher)


def emit_summary_failed_fallback_passthrough(
    req_id: str, err: str = "",
    dispatcher: Optional[Any] = None,
) -> None:
    return emit_summary_event(req_id, "execution.summary.summary_failed_fallback_passthrough",
                         level="ERROR",
                         err=err or None, dispatcher=dispatcher)
