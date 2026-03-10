from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from .config import SAFETY_MARGIN_TOK, SUMMARY_MAX_TOKENS, UPSTREAM_BASE_URL
from .logger import log, snip_json
from .upstream import http_client, get_ctx_len_for_model
from .token_counter import TokenCounter


# ---------------------------------------------------------------------
# Summary prompt config
# ---------------------------------------------------------------------

SUMMARY_PROMPT_DIR = os.getenv("SUMMARY_PROMPT_DIR", "./prompts")
SUMMARY_PROMPT_TYPE = os.getenv("SUMMARY_PROMPT_TYPE", "curated")
SUMMARY_TEMPERATURE = float(os.getenv("SUMMARY_TEMPERATURE", "0.2"))
MAX_SUMMARY_BACKEND_ATTEMPTS = int(os.getenv("MAX_SUMMARY_BACKEND_ATTEMPTS", "8"))


# ----------------------------
# Rolling summary (decision + repack)
# ----------------------------

MAX_HEAD = int(os.getenv("MAX_HEAD", "3"))
MAX_TAIL = int(os.getenv("MAX_TAIL", "3"))
SUMMARY_PIN_FIRST_USER = os.getenv("SUMMARY_PIN_FIRST_USER", "1").strip().lower() not in {"0", "false", "no", "off"}

# We need to budget how many tokens the inserted summary will take in the prompt.
# Default: tie it to SUMMARY_MAX_TOKENS (the summary model output cap), conservative.
SUMMARY_INSERT_BUDGET_TOK = int(os.getenv("SUMMARY_INSERT_BUDGET_TOK", str(SUMMARY_MAX_TOKENS)))


DEFAULT_SUMMARY_PROMPTS: Dict[str, str] = {
    "classic": """Sei un assistente che produce un RIASSUNTO DI CONTESTO per un altro modello.

Obiettivo:
comprimere la parte centrale della conversazione preservando fatti, nomi, decisioni, richieste, vincoli, TODO e dettagli tecnici utili.

Regole:
- non inventare
- mantieni lingua coerente (preferisci {{LANG_HINT}})
- sii breve ma denso
- usa bullet points se utile
- includi decisioni e TODO ancora aperti
- non aggiungere commenti extra

Transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===

RISPOSTA:
solo il riassunto finale, senza prefazioni.
""",
    "structured": """Sei un motore di compressione del contesto.

Obiettivo:
trasformare la parte centrale della conversazione in uno stato strutturato e compatto, utile per proseguire correttamente il dialogo.

Regole:
- non inventare
- mantieni lingua coerente (preferisci {{LANG_HINT}})
- preferisci bullet points brevi
- separa chiaramente fatti, decisioni, vincoli e attività aperte
- includi dettagli tecnici solo se realmente utili
- niente prose introduttive

Formato di output obbligatorio:

[STATUS]

FACTS:
- ...

DECISIONS:
- ...

CONSTRAINTS:
- ...

OPEN_TASKS:
- ...

STYLE_NOTES:
- ...

[/STATUS]

Transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===
""",
    "curated": """You are a context compaction engine.

Your task is NOT to simply summarize a conversation.

Your task is to produce a compressed reconstruction of the conversation that preserves the information necessary to continue the discussion correctly.

The reconstruction must balance:
- verbatim preservation of critical passages
- summarized sections for less critical spans
- a structured status snapshot

The output must be concise but faithful.

COMPACTION STRATEGY:
1. Preserve VERBATIM when content is critical:
   - instructions given to the assistant
   - constraints or rules
   - technical specifications
   - examples of style or tone
   - key decisions
   - code snippets
   - prompts or templates

2. Summarize when content is:
   - repetitive
   - exploratory discussion
   - background reasoning
   - intermediate brainstorming

3. Prefer short bullet summaries rather than prose.

4. Preserve the latest part of the provided transcript verbatim only if especially useful for continuity.
   Do NOT overuse verbatim excerpts.

5. Extract a STATUS block describing the current state.

OUTPUT FORMAT:

[INIT_INSTRUCTIONS_RAW]
Verbatim excerpts of important initial instructions or constraints.
Keep only the most relevant ones.
Maximum: 2 excerpts.
[/INIT_INSTRUCTIONS_RAW]

[ARCHIVE_SUMMARY]
Bullet summary of older conversation parts that are not critical to keep verbatim.
Focus on facts, reasoning steps, outcomes, technical constraints and conclusions.
Maximum: 10 bullets.
[/ARCHIVE_SUMMARY]

[KEY_EXCERPTS_RAW]
Short verbatim excerpts that are especially important to preserve.
Maximum: 3 excerpts.
[/KEY_EXCERPTS_RAW]

[STATUS]

FACTS:
- ...

DECISIONS:
- ...

CONSTRAINTS:
- ...

OPEN_TASKS:
- ...

STYLE_NOTES:
- ...

[/STATUS]

RULES:
- Be concise.
- Do NOT invent information.
- Do NOT repeat the entire transcript.
- Preserve key technical content if present.
- If unsure whether something is important, summarize it instead of preserving verbatim.
- Keep the output compact.

Transcript:

=== TRANSCRIPT START ===
{{TRANSCRIPT}}
=== TRANSCRIPT END ===
"""
}




def render_summary_prompt(
    transcript: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "italiano",
) -> str:
    template = load_summary_prompt_template(prompt_type=prompt_type)
    return (
        template
        .replace("{{TRANSCRIPT}}", transcript)
        .replace("{{LANG_HINT}}", lang_hint)
    )


def get_summary_system_prompt(prompt_type: Optional[str] = None) -> str:
    """
    Keep system prompt small and stable.
    The real task instructions live in the file-based user template.
    """
    effective_type = (prompt_type or SUMMARY_PROMPT_TYPE or "curated").strip()

    if effective_type == "classic":
        return (
            "Sei un assistente che comprime conversazioni per un altro modello. "
            "Non inventare nulla. Sii fedele, compatto e utile."
        )

    if effective_type == "structured":
        return (
            "Sei un assistente che trasforma conversazioni in stato strutturato compatto. "
            "Non inventare nulla. Mantieni solo ciò che è utile a continuare la conversazione."
        )

    return (
        "You are a context compaction engine. "
        "Be faithful, compact, structured, and do not invent information."
    )


def render_incremental_summary_prompt(
    existing_summary: str,
    new_messages: List[Dict[str, Any]],
    *,
    lang_hint: str = "italiano",
) -> str:
    transcript = render_messages_for_summary(new_messages)
    
    # Load from file
    path = Path(SUMMARY_PROMPT_DIR) / "incremental.txt"
    try:
        template = path.read_text(encoding="utf-8")
    except Exception:
        # fallback to default prompt if file not found
        template = """Sei un assistente che aggiorna un riassunto di contesto per un altro modello.

Obiettivo:
integrare il riassunto esistente con i nuovi messaggi, preservando fatti, vincoli, decisioni, richieste e TODO ancora aperti.

Regole:
- non inventare
- mantieni lingua coerente (preferisci {{LANG_HINT}})
- sii compatto ma fedele
- integra il nuovo contenuto nel summary esistente invece di limitarti ad accodarlo
- restituisci solo il summary aggiornato

=== EXISTING SUMMARY START ===
{{EXISTING_SUMMARY}}
=== EXISTING SUMMARY END ===

=== NEW MESSAGES START ===
{{NEW_MESSAGES}}
=== NEW MESSAGES END ==="""
    
    return (
        template
        .replace("{{EXISTING_SUMMARY}}", existing_summary)
        .replace("{{NEW_MESSAGES}}", transcript)
        .replace("{{LANG_HINT}}", lang_hint)
    )


@dataclass(frozen=True)
class SummarizePlan:
    should: bool
    reason: str
    threshold: int
    prompt_tok_est: int
    head_n: int
    tail_n: int
    middle_count: int
    repacked_tok_est: int
    pinned_head_n: int = 0


def split_messages(messages: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if not system_msgs:
        return None, non_system

    merged = ""
    for sm in system_msgs:
        c = sm.get("content", "")
        if isinstance(c, str) and c.strip():
            merged += c.strip() + "\n\n"
    merged = merged.strip()

    return {"role": "system", "content": merged}, non_system


def _estimate_tokens_for_msgs(tok: TokenCounter, msgs: List[Dict[str, Any]]) -> int:
    try:
        return tok.count_messages(msgs)
    except Exception:
        # fallback rough estimate
        s = ""
        for m in msgs:
            c = m.get("content", "")
            if isinstance(c, str):
                s += c
        return max(1, int(len(s) / 4))


def _pinned_head_count(non_system: List[Dict[str, Any]], pin_first_user: bool = SUMMARY_PIN_FIRST_USER) -> int:
    if not pin_first_user:
        return 0
    for idx, msg in enumerate(non_system):
        if msg.get("role") == "user":
            return idx + 1
    return 0


def _estimate_repacked_tokens(
    tok: TokenCounter,
    *,
    sys_msg: Optional[Dict[str, Any]],
    head: List[Dict[str, Any]],
    tail: List[Dict[str, Any]],
    summary_budget_tok: int,
) -> int:
    # Estimate tokens for system+head+tail messages, plus:
    # - overhead for inserting a "summary system message"
    # - budget tokens for the summary content itself.
    msgs: List[Dict[str, Any]] = []
    if sys_msg:
        msgs.append(sys_msg)
    msgs.extend(head)
    msgs.extend(tail)
    base = _estimate_tokens_for_msgs(tok, msgs)

    # Message overhead + placeholder content (budgeted separately)
    # We'll add a small fixed overhead for the inserted message itself.
    return base + summary_budget_tok + 16


def _choose_head_tail(
    tok: TokenCounter,
    *,
    sys_msg: Optional[Dict[str, Any]],
    non_system: List[Dict[str, Any]],
    threshold: int,
    max_head: int,
    max_tail: int,
    summary_budget_tok: int,
    pinned_head_n: int = 0,
) -> Tuple[int, int, int]:
    """Return (head_n, tail_n, repacked_tok_est) that fits threshold if possible.

    Strategy: maximize kept messages, bounded by max_head/max_tail, while ensuring a non-empty middle.
    """
    n = len(non_system)
    if n <= 2:
        return (0, 0, _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=[], tail=[], summary_budget_tok=summary_budget_tok))

    best: Tuple[int, int, int] | None = None

    # Try larger totals first
    min_head = min(max(0, pinned_head_n), n - 1)
    for total in range(min(max_head + max_tail, n), 1, -1):
        # split total between head and tail
        head_max_this = min(max_head, total - 1)  # keep at least 1 for tail
        for head_n in range(head_max_this, min_head - 1, -1):
            tail_n = total - head_n
            if tail_n < 0:
                continue
            if tail_n > max_tail:
                continue

            # Need a non-empty middle to summarize
            if head_n + tail_n >= n:
                continue
            middle_count = n - head_n - tail_n
            if middle_count <= 0:
                continue

            head = non_system[:head_n]
            tail = non_system[-tail_n:] if tail_n > 0 else []
            est = _estimate_repacked_tokens(
                tok,
                sys_msg=sys_msg,
                head=head,
                tail=tail,
                summary_budget_tok=summary_budget_tok,
            )
            if est <= threshold:
                best = (head_n, tail_n, est)
                return best

    # If nothing fits, fall back to minimal head/tail (still useful for summarization)
    head_n = min(max(min_head, 1 if n > 1 else 0), max_head, n - 1)
    tail_n = min(1, max_tail, n - head_n)
    # Ensure we still have a middle
    if head_n + tail_n >= n:
        # squeeze while preserving the pinned prefix if possible
        head_n = min_head
        tail_n = min(1, max_tail, max(0, n - head_n - 1))

    head = non_system[:head_n] if head_n else []
    tail = non_system[-tail_n:] if tail_n else []
    est = _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=head, tail=tail, summary_budget_tok=summary_budget_tok)
    return (head_n, tail_n, est)


def should_summarise(
    *,
    tok: TokenCounter,
    messages: List[Dict[str, Any]],
    ctx_eff: int,
    max_out: int,
    safety_margin_tok: int = SAFETY_MARGIN_TOK,
    max_head: int = MAX_HEAD,
    max_tail: int = MAX_TAIL,
    summary_insert_budget_tok: int = SUMMARY_INSERT_BUDGET_TOK,
    pin_first_user: bool = SUMMARY_PIN_FIRST_USER,
) -> SummarizePlan:
    """Make the summarization decision and choose dynamic head/tail sizes.

    All summarization logic lives here (decision + sizing + accounting).
    """
    threshold = max(256, int(ctx_eff) - int(max_out) - int(safety_margin_tok))
    prompt_tok_est = _estimate_tokens_for_msgs(tok, messages)

    sys_msg, non_system = split_messages(messages)
    n = len(non_system)
    pinned_head_n = _pinned_head_count(non_system, pin_first_user=pin_first_user)

    # Not enough to justify summarizing
    if prompt_tok_est <= threshold:
        return SummarizePlan(
            should=False,
            reason="prompt_within_threshold",
            threshold=threshold,
            prompt_tok_est=prompt_tok_est,
            head_n=0,
            tail_n=0,
            middle_count=max(0, n),
            repacked_tok_est=prompt_tok_est,
            pinned_head_n=pinned_head_n,
        )

    # Need enough non-system msgs to have head + middle + tail
    if n < 3:
        return SummarizePlan(
            should=False,
            reason="too_few_messages",
            threshold=threshold,
            prompt_tok_est=prompt_tok_est,
            head_n=0,
            tail_n=0,
            middle_count=max(0, n),
            repacked_tok_est=prompt_tok_est,
            pinned_head_n=pinned_head_n,
        )

    head_n, tail_n, repacked_est = _choose_head_tail(
        tok,
        sys_msg=sys_msg,
        non_system=non_system,
        threshold=threshold,
        max_head=max_head,
        max_tail=max_tail,
        summary_budget_tok=summary_insert_budget_tok,
        pinned_head_n=pinned_head_n,
    )
    middle_count = max(0, n - head_n - tail_n)

    # Only summarize if we actually have a middle to summarize
    if middle_count <= 0:
        return SummarizePlan(
            should=False,
            reason="no_middle",
            threshold=threshold,
            prompt_tok_est=prompt_tok_est,
            head_n=head_n,
            tail_n=tail_n,
            middle_count=0,
            repacked_tok_est=prompt_tok_est,
            pinned_head_n=pinned_head_n,
        )

    
    plan = SummarizePlan(
        should=True,
        reason="prompt_exceeds_threshold",
        threshold=threshold,
        prompt_tok_est=prompt_tok_est,
        head_n=head_n,
        tail_n=tail_n,
        middle_count=middle_count,
        repacked_tok_est=repacked_est,
        pinned_head_n=pinned_head_n,
    )
    log(
        "INFO",
        "summary_plan",
        should=True,
        reason="prompt_exceeds_threshold",
        threshold=threshold,
        prompt_tok_est=prompt_tok_est,
        head_n=head_n,
        tail_n=tail_n,
        middle_count=middle_count,
        repacked_tok_est=repacked_est,
        pinned_head_n=pinned_head_n,
    )
    return plan


def render_messages_for_summary(messages: List[Dict[str, Any]], max_chars: int = 12000) -> str:
    lines: List[str] = []
    used = 0
    for m in messages:
        role = (m.get("role") or "unknown").upper()
        content = m.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: List[str] = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
            text = "\n".join(parts)
        else:
            text = ""

        text = text.strip()
        if not text:
            continue

        line = f"{role}: {text}"
        lines.append(line)
        used += len(line)
        if used > max_chars:
            lines.append("... (truncated)")
            break
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Summary call
# ---------------------------------------------------------------------

async def _request_summary_completion(body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    client = await http_client()
    r = await client.post(url, json=body)
    r.raise_for_status()
    return r.json()


def _extract_backend_ctx_error_message(err: Exception) -> str:
    resp = getattr(err, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return getattr(resp, "text", "") or str(err)
    return str(err)


def _http_status_from_error(err: Exception) -> int | None:
    resp = getattr(err, "response", None)
    status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        return status
    return None


def _is_context_overflow_error(err: Exception) -> bool:
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
    summary_ctx = max(512, int(summary_ctx))
    return max(512, summary_ctx // 2)


def _messages_signature(messages: List[Dict[str, Any]]) -> tuple[int, int, tuple[tuple[str, int], ...]]:
    sig: List[tuple[str, int]] = []
    total_chars = 0
    for msg in messages:
        role = str(msg.get("role") or "")
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    val = item.get("text")
                    if isinstance(val, str):
                        parts.append(val)
            text = "\n".join(parts)
        else:
            text = ""
        total_chars += len(text)
        sig.append((role, len(text)))
    return (len(messages), total_chars, tuple(sig))


def _split_single_message_for_retry(msg: Dict[str, Any]) -> List[Dict[str, Any]]:
    content = msg.get("content", "")
    if isinstance(content, str) and len(content) > 1:
        mid = max(1, len(content) // 2)
        left = content[:mid].rstrip() or content[:mid]
        right = content[mid:].lstrip() or content[mid:]
        if left and right:
            return [{**msg, "content": left}, {**msg, "content": right}]
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                val = item.get("text")
                if isinstance(val, str):
                    text_parts.append(val)
        joined = "\n".join(text_parts)
        if len(joined) > 1:
            mid = max(1, len(joined) // 2)
            left = joined[:mid].rstrip() or joined[:mid]
            right = joined[mid:].lstrip() or joined[mid:]
            if left and right and left != right:
                return [{**msg, "content": left}, {**msg, "content": right}]
    return [msg]


def _force_split_messages(messages: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if len(messages) > 1:
        mid = max(1, len(messages) // 2)
        left = messages[:mid]
        right = messages[mid:]
        return [left, right]
    if not messages:
        return []
    split = _split_single_message_for_retry(messages[0])
    if len(split) > 1:
        return [[split[0]], [split[1]]]
    return [messages]


def _normalize_retry_chunks(messages: List[Dict[str, Any]], chunks: List[List[Dict[str, Any]]]) -> tuple[List[List[Dict[str, Any]]], str]:
    original_sig = _messages_signature(messages)
    if not chunks:
        forced = _force_split_messages(messages)
        return (forced, "empty_chunk_result")
    if len(chunks) == 1 and _messages_signature(chunks[0]) == original_sig:
        forced = _force_split_messages(messages)
        if len(forced) > 1:
            return (forced, "forced_split_no_progress")
        return (chunks, "single_chunk_no_progress")
    return (chunks, "ok")


async def _should_prechunk_summary_call(
    messages: List[Dict[str, Any]],
    *,
    summary_model: str,
    prompt_type: Optional[str],
    lang_hint: str,
    incremental_existing_summary: str | None = None,
) -> tuple[bool, int, int]:
    summary_ctx = await get_ctx_len_for_model(summary_model)
    threshold = max(128, int(summary_ctx) - int(SUMMARY_MAX_TOKENS) - int(SAFETY_MARGIN_TOK))
    if incremental_existing_summary is None:
        user = render_summary_prompt(render_messages_for_summary(messages), prompt_type=prompt_type, lang_hint=lang_hint)
        body_msgs = [
            {"role": "system", "content": get_summary_system_prompt(prompt_type=prompt_type)},
            {"role": "user", "content": user},
        ]
    else:
        user = render_incremental_summary_prompt(incremental_existing_summary, messages, lang_hint=lang_hint)
        body_msgs = [
            {"role": "system", "content": "Sei un assistente che aggiorna un riassunto di contesto per un altro modello. Non inventare nulla. Mantieni il risultato compatto e fedele."},
            {"role": "user", "content": user},
        ]
    est_tokens = _estimate_tokens_for_msgs(TokenCounter(), body_msgs)
    return (est_tokens > threshold, est_tokens, threshold)


def _split_text_preserve_lines(text: str, max_chars: int) -> List[str]:
    text = text or ""
    if len(text) <= max_chars:
        return [text]
    parts: List[str] = []
    cur = ""
    for line in text.splitlines(True):
        if len(cur) + len(line) <= max_chars:
            cur += line
            continue
        if cur:
            parts.append(cur)
            cur = ""
        while len(line) > max_chars:
            parts.append(line[:max_chars])
            line = line[max_chars:]
        cur = line
    if cur:
        parts.append(cur)
    return [p for p in parts if p]


def _split_oversized_message(msg: Dict[str, Any], max_chars: int) -> List[Dict[str, Any]]:
    content = msg.get("content", "")
    role = msg.get("role")
    if isinstance(content, str):
        parts = _split_text_preserve_lines(content, max_chars=max(400, max_chars))
        return [{**msg, "content": part} for part in parts]
    if isinstance(content, list):
        text_parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                txt = item.get("text")
                if isinstance(txt, str):
                    text_parts.append(txt)
        joined = "\n".join(text_parts)
        parts = _split_text_preserve_lines(joined, max_chars=max(400, max_chars))
        return [{**msg, "content": part, "role": role} for part in parts]
    return [msg]


def _chunk_messages_for_summary(
    messages: List[Dict[str, Any]],
    *,
    prompt_type: Optional[str],
    lang_hint: str,
    summary_model_ctx: int,
    incremental_existing_summary: str | None = None,
) -> List[List[Dict[str, Any]]]:
    threshold = max(128, int(summary_model_ctx) - int(SUMMARY_MAX_TOKENS) - int(SAFETY_MARGIN_TOK))
    max_chars_single = max(800, threshold * 4)
    expanded: List[Dict[str, Any]] = []
    for m in messages:
        expanded.extend(_split_oversized_message(m, max_chars_single))

    chunks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []

    def est(msgs: List[Dict[str, Any]]) -> int:
        if incremental_existing_summary is None:
            user = render_summary_prompt(render_messages_for_summary(msgs), prompt_type=prompt_type, lang_hint=lang_hint)
            body_msgs = [
                {"role": "system", "content": get_summary_system_prompt(prompt_type=prompt_type)},
                {"role": "user", "content": user},
            ]
        else:
            user = render_incremental_summary_prompt(incremental_existing_summary, msgs, lang_hint=lang_hint)
            body_msgs = [
                {"role": "system", "content": "Sei un assistente che aggiorna un riassunto di contesto per un altro modello. Non inventare nulla. Mantieni il risultato compatto e fedele."},
                {"role": "user", "content": user},
            ]
        return _estimate_tokens_for_msgs(TokenCounter(), body_msgs)

    for msg in expanded:
        candidate = cur + [msg]
        if not cur or est(candidate) <= threshold:
            cur = candidate
            continue
        chunks.append(cur)
        cur = [msg]
    if cur:
        chunks.append(cur)
    return chunks or [expanded]


async def _summarize_middle_core(
    middle: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "italiano",
) -> str:
    transcript = render_messages_for_summary(middle)
    sys = get_summary_system_prompt(prompt_type=prompt_type)
    user = render_summary_prompt(transcript, prompt_type=prompt_type, lang_hint=lang_hint)
    body = {
        "model": summary_model,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        "temperature": SUMMARY_TEMPERATURE,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "stream": False,
    }
    log("INFO", "summary_req", req_id=req_id, summary_model=summary_model, summary_prompt_type=(prompt_type or SUMMARY_PROMPT_TYPE), middle_count=len(middle), transcript_chars=len(transcript), body_json=snip_json(body))
    t0 = time.time()
    data = await _request_summary_completion(body)
    elapsed_ms = (time.time() - t0) * 1000.0
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        raise RuntimeError(json.dumps(data, ensure_ascii=False))
    try:
        summary = data["choices"][0]["message"]["content"]
    except Exception:
        summary = ""
    summary = _sanitize_summary_text(summary, fallback="(Contesto compattato non disponibile.)") or "(Contesto compattato non disponibile.)"
    log("INFO", "summary_reply", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), usage=data.get("usage"), summary_chars=len(summary), summary_snip=summary, raw_json=snip_json(data))
    return summary


async def summarize_middle(
    middle: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    prompt_type: Optional[str] = None,
    lang_hint: str = "italiano",
    _attempt: int = 0,
) -> str:
    if _attempt >= MAX_SUMMARY_BACKEND_ATTEMPTS:
        log("ERROR", "summary_retry_exhausted", req_id=req_id, summary_model=summary_model, attempts=_attempt, max_attempts=MAX_SUMMARY_BACKEND_ATTEMPTS, middle_count=len(middle))
        raise RuntimeError(f"summary retry exhausted after {MAX_SUMMARY_BACKEND_ATTEMPTS} attempts")

    should_prechunk, est_tokens, threshold = await _should_prechunk_summary_call(
        middle,
        summary_model=summary_model,
        prompt_type=prompt_type,
        lang_hint=lang_hint,
        incremental_existing_summary=None,
    )
    if should_prechunk:
        summary_ctx = await get_ctx_len_for_model(summary_model)
        chunks = _chunk_messages_for_summary(middle, prompt_type=prompt_type, lang_hint=lang_hint, summary_model_ctx=summary_ctx)
        chunks, normalization_reason = _normalize_retry_chunks(middle, chunks)
        log("WARN", "summary_preflight_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, est_tokens=est_tokens, threshold=threshold, normalization=normalization_reason)
        if normalization_reason == "forced_split_no_progress":
            log("WARN", "summary_preflight_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
        if normalization_reason == "single_chunk_no_progress":
            log("ERROR", "summary_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, est_tokens=est_tokens, threshold=threshold)
            raise RuntimeError("summary preflight produced no-progress single chunk")
        partials: List[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            partials.append(await summarize_middle(chunk, f"{req_id}-c{idx}", summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1))
        merge_messages = [{"role": "user", "content": f"[PARTIAL SUMMARY {i}]\n{s}"} for i, s in enumerate(partials, start=1)]
        if len(merge_messages) == 1:
            return partials[0]
        return await summarize_middle(merge_messages, req_id=f"{req_id}-merge", summary_model=summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1)

    try:
        return await _summarize_middle_core(middle, req_id, summary_model, prompt_type=prompt_type, lang_hint=lang_hint)
    except Exception as err:
        summary_ctx = await get_ctx_len_for_model(summary_model)
        retry_reason = "overflow" if _is_context_overflow_error(err) else "http_retry" if _should_retry_with_reduced_context(err) else "fatal"
        if retry_reason == "overflow":
            chunks = _chunk_messages_for_summary(middle, prompt_type=prompt_type, lang_hint=lang_hint, summary_model_ctx=summary_ctx)
            chunks, normalization_reason = _normalize_retry_chunks(middle, chunks)
            log("WARN", "summary_overflow_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, normalization=normalization_reason)
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_overflow_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        elif retry_reason == "http_retry":
            reduced_ctx = _reduced_ctx_for_retry(summary_ctx)
            chunks = _chunk_messages_for_summary(middle, prompt_type=prompt_type, lang_hint=lang_hint, summary_model_ctx=reduced_ctx)
            chunks, normalization_reason = _normalize_retry_chunks(middle, chunks)
            log("WARN", "summary_http_retry_reduced_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, status=_http_status_from_error(err), reduced_ctx=reduced_ctx, err=_extract_backend_ctx_error_message(err), normalization=normalization_reason)
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_http_retry_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        else:
            raise
        partials: List[str] = []
        for idx, chunk in enumerate(chunks, start=1):
            partials.append(await summarize_middle(chunk, f"{req_id}-c{idx}", summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1))
        merge_messages = [{"role": "user", "content": f"[PARTIAL SUMMARY {i}]\n{s}"} for i, s in enumerate(partials, start=1)]
        if len(merge_messages) == 1:
            return partials[0]
        return await summarize_middle(merge_messages, req_id=f"{req_id}-merge", summary_model=summary_model, prompt_type=prompt_type, lang_hint=lang_hint, _attempt=_attempt + 1)


async def _summarize_incremental_core(
    existing_summary: str,
    new_messages: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    lang_hint: str = "italiano",
) -> str:
    sys = (
        "Sei un assistente che aggiorna un riassunto di contesto per un altro modello. "
        "Non inventare nulla. Mantieni il risultato compatto e fedele."
    )
    user = render_incremental_summary_prompt(existing_summary, new_messages, lang_hint=lang_hint)
    body = {
        "model": summary_model,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        "temperature": SUMMARY_TEMPERATURE,
        "max_tokens": SUMMARY_MAX_TOKENS,
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
    summary = _sanitize_summary_text(summary, fallback=existing_summary.strip() or "(Contesto compattato non disponibile.)") or existing_summary.strip() or "(Contesto compattato non disponibile.)"
    log("INFO", "summary_reply", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), usage=data.get("usage"), summary_chars=len(summary), summary_snip=summary, raw_json=snip_json(data))
    return summary


async def summarize_incremental(
    existing_summary: str,
    new_messages: List[Dict[str, Any]],
    req_id: str,
    summary_model: str,
    *,
    lang_hint: str = "italiano",
    _attempt: int = 0,
) -> str:
    if _attempt >= MAX_SUMMARY_BACKEND_ATTEMPTS:
        log("ERROR", "summary_incremental_retry_exhausted", req_id=req_id, summary_model=summary_model, attempts=_attempt, max_attempts=MAX_SUMMARY_BACKEND_ATTEMPTS, new_messages_count=len(new_messages))
        raise RuntimeError(f"incremental summary retry exhausted after {MAX_SUMMARY_BACKEND_ATTEMPTS} attempts")

    should_prechunk, est_tokens, threshold = await _should_prechunk_summary_call(
        new_messages,
        summary_model=summary_model,
        prompt_type=None,
        lang_hint=lang_hint,
        incremental_existing_summary=existing_summary,
    )
    if should_prechunk:
        summary_ctx = await get_ctx_len_for_model(summary_model)
        chunks = _chunk_messages_for_summary(new_messages, prompt_type=None, lang_hint=lang_hint, summary_model_ctx=summary_ctx, incremental_existing_summary=existing_summary)
        chunks, normalization_reason = _normalize_retry_chunks(new_messages, chunks)
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

    try:
        return await _summarize_incremental_core(existing_summary, new_messages, req_id, summary_model, lang_hint=lang_hint)
    except Exception as err:
        summary_ctx = await get_ctx_len_for_model(summary_model)
        retry_reason = "overflow" if _is_context_overflow_error(err) else "http_retry" if _should_retry_with_reduced_context(err) else "fatal"
        if retry_reason == "overflow":
            chunks = _chunk_messages_for_summary(new_messages, prompt_type=None, lang_hint=lang_hint, summary_model_ctx=summary_ctx, incremental_existing_summary=existing_summary)
            chunks, normalization_reason = _normalize_retry_chunks(new_messages, chunks)
            log("WARN", "summary_incremental_overflow_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, normalization=normalization_reason)
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_incremental_overflow_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_incremental_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        elif retry_reason == "http_retry":
            reduced_ctx = _reduced_ctx_for_retry(summary_ctx)
            chunks = _chunk_messages_for_summary(new_messages, prompt_type=None, lang_hint=lang_hint, summary_model_ctx=reduced_ctx, incremental_existing_summary=existing_summary)
            chunks, normalization_reason = _normalize_retry_chunks(new_messages, chunks)
            log("WARN", "summary_incremental_http_retry_reduced_chunking", req_id=req_id, chunks=len(chunks), summary_model=summary_model, status=_http_status_from_error(err), reduced_ctx=reduced_ctx, err=_extract_backend_ctx_error_message(err), normalization=normalization_reason)
            if normalization_reason == "forced_split_no_progress":
                log("WARN", "summary_incremental_http_retry_forced_split", req_id=req_id, chunks=len(chunks), summary_model=summary_model)
            if normalization_reason == "single_chunk_no_progress":
                log("ERROR", "summary_incremental_no_progress_abort", req_id=req_id, summary_model=summary_model, attempts=_attempt + 1, err=_extract_backend_ctx_error_message(err))
                raise
        else:
            raise
        current = existing_summary
        for idx, chunk in enumerate(chunks, start=1):
            current = await summarize_incremental(current, chunk, f"{req_id}-c{idx}", summary_model, lang_hint=lang_hint, _attempt=_attempt + 1)
        return current


def _sanitize_summary_text(text: str, *, fallback: str = "") -> str:
    out = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not out:
        return (fallback or "").strip()

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
    s = (text or "").strip().lower()
    if not s:
        return True
    placeholders = [
        "(contesto compattato non disponibile.)",
        "context unavailable",
        "summary unavailable",
    ]
    return s in placeholders


def is_summary_cacheable(text: str) -> bool:
    s = _sanitize_summary_text(text or "")
    if is_summary_placeholder(s):
        return False
    # avoid caching empty / near-empty accidental outputs
    if len(s) < 8:
        return False
    return True
# ---------------------------------------------------------------------
# Repacking
# ---------------------------------------------------------------------

# ---------------------------------------------------------------------
# Repacking
# ---------------------------------------------------------------------

def build_repacked_messages(
    original: List[Dict[str, Any]],
    *,
    summary_text: str,
    head_n: int,
    tail_n: int,
    pinned_head_n: int = 0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Return (repacked_messages, middle_messages_used_for_summary).

    Strategy:
    - preserve original system message if present
    - preserve head raw
    - inject archived/compacted middle as a dedicated system block
    - preserve tail raw
    """
    sys_msg, non_system = split_messages(original)

    n = len(non_system)
    head_n = max(int(pinned_head_n), min(int(head_n), n))
    tail_n = max(0, min(int(tail_n), n - head_n))

    head = non_system[:head_n] if head_n else []
    tail = non_system[-tail_n:] if tail_n else []
    middle = non_system[head_n : n - tail_n] if (head_n + tail_n) < n else []

    if not middle:
        merged: List[Dict[str, Any]] = []
        if sys_msg:
            merged.append(sys_msg)
        merged.extend(non_system)
        return merged, []

    repacked: List[Dict[str, Any]] = []

    if sys_msg:
        repacked.append(sys_msg)

    repacked.extend(head)

    archived_block = (
        "[ARCHIVED_COMPACT_CONTEXT]\n"
        "The following block is a compressed reconstruction of earlier conversation content.\n"
        "Treat it as authoritative context for continuity, decisions, constraints, facts and pending work.\n"
        "Prefer this block over trying to infer missing older details from the recent tail alone.\n\n"
        f"{summary_text.strip()}\n"
        "[/ARCHIVED_COMPACT_CONTEXT]"
    )

    repacked.append({
        "role": "system",
        "content": archived_block,
    })

    repacked.extend(tail)
    return repacked, middle


def build_archived_summary_message(summary_text: str) -> Dict[str, Any]:
    
    # Load from file
    path = Path(SUMMARY_PROMPT_DIR) / "archived_block.txt"
    try:
        template = path.read_text(encoding="utf-8")
    except Exception:
        # fallback to default prompt if file not found
        template = "[ARCHIVED_COMPACT_CONTEXT]\n" + \
            "The following block is a compressed reconstruction of earlier conversation content.\n" + \
            "Treat it as authoritative context for continuity, decisions, constraints, facts and pending work.\n" + \
            "Prefer this block over trying to infer missing older details from the recent tail alone.\n\n" + \
            "{{SUMMARY_TEXT}}\n" + \
            "[/ARCHIVED_COMPACT_CONTEXT]"
    
    archived_block = template.replace("{{SUMMARY_TEXT}}", summary_text.strip())
    return {"role": "system", "content": archived_block}


def build_messages_from_summary_prefix(
    original: List[Dict[str, Any]],
    *,
    summary_text: str,
    covered_end_idx: int,
    append_until_idx: int,
    pinned_head_n: int = 0,
) -> List[Dict[str, Any]]:
    sys_msg, non_system = split_messages(original)
    repacked: List[Dict[str, Any]] = []
    if sys_msg:
        repacked.append(sys_msg)
    if non_system and pinned_head_n > 0:
        repacked.extend(non_system[:pinned_head_n])
    repacked.append(build_archived_summary_message(summary_text))
    if non_system:
        start = max(int(pinned_head_n), covered_end_idx + 1)
        end = min(len(non_system) - 1, append_until_idx)
        if end >= start:
            repacked.extend(non_system[start : end + 1])
    return repacked




def ensure_repacked_has_user_message(
    repacked: List[Dict[str, Any]],
    original: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if any(isinstance(m, dict) and m.get("role") == "user" for m in repacked):
        return repacked
    _sys_msg, non_system = split_messages(original)
    last_user: Dict[str, Any] | None = None
    for m in reversed(non_system):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = m
            break
    if last_user is None:
        return repacked
    return [*repacked, last_user]


def choose_append_until_idx(
    *,
    tok: TokenCounter,
    original: List[Dict[str, Any]],
    summary_text: str,
    covered_end_idx: int,
    threshold: int,
    pinned_head_n: int = 0,
) -> int:
    sys_msg, non_system = split_messages(original)
    if not non_system:
        return covered_end_idx
    last_idx = len(non_system) - 1
    if last_idx - covered_end_idx <= 1:
        return last_idx
    best = min(covered_end_idx, last_idx)
    for idx in range(covered_end_idx + 1, len(non_system)):
        repacked = build_messages_from_summary_prefix(
            original,
            summary_text=summary_text,
            covered_end_idx=covered_end_idx,
            append_until_idx=idx,
            pinned_head_n=pinned_head_n,
        )
        est = _estimate_tokens_for_msgs(tok, repacked)
        if est <= threshold:
            best = idx
        else:
            break
    if best < covered_end_idx:
        best = covered_end_idx
    return best
