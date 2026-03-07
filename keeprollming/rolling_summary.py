from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import SAFETY_MARGIN_TOK, SUMMARY_MAX_TOKENS, UPSTREAM_BASE_URL
from .logger import log, snip_json
from .token_counter import TokenCounter
from .upstream import get_ctx_len_for_model, http_client


SUMMARY_PROMPT_DIR = os.getenv("SUMMARY_PROMPT_DIR", "./prompts")
SUMMARY_PROMPT_TYPE = os.getenv("SUMMARY_PROMPT_TYPE", "curated")
SUMMARY_TEMPERATURE = float(os.getenv("SUMMARY_TEMPERATURE", "0.2"))
MAX_HEAD = int(os.getenv("MAX_HEAD", "3"))
MAX_TAIL = int(os.getenv("MAX_TAIL", "3"))
SUMMARY_INSERT_BUDGET_TOK = int(os.getenv("SUMMARY_INSERT_BUDGET_TOK", str(SUMMARY_MAX_TOKENS)))
SUMMARY_OVERFLOW_MAX_PASSES = max(1, int(os.getenv("SUMMARY_OVERFLOW_MAX_PASSES", "3")))

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
""",
}


INCREMENTAL_SUMMARY_PROMPT = """You are updating an existing compact context block for another model.

Goal:
merge the existing compact summary with the new messages so the result remains compact, faithful and useful for continuing the conversation.

Rules:
- do not invent information
- preserve facts, decisions, constraints, style notes and open tasks
- remove redundancy
- prefer concise bullets or compact structured text
- keep the language coherent with the existing summary
- output only the updated summary

=== EXISTING SUMMARY START ===
{{EXISTING_SUMMARY}}
=== EXISTING SUMMARY END ===

=== NEW MESSAGES START ===
{{TRANSCRIPT}}
=== NEW MESSAGES END ===
"""


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


def load_summary_prompt_template(prompt_type: Optional[str] = None) -> str:
    effective_type = (prompt_type or SUMMARY_PROMPT_TYPE or "curated").strip()
    prompt_dir = Path(SUMMARY_PROMPT_DIR)
    prompt_file = prompt_dir / f"{effective_type}.summary_prompt.txt"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    return DEFAULT_SUMMARY_PROMPTS.get(effective_type, DEFAULT_SUMMARY_PROMPTS["curated"])


def render_summary_prompt(transcript: str, *, prompt_type: Optional[str] = None, lang_hint: str = "italiano") -> str:
    template = load_summary_prompt_template(prompt_type=prompt_type)
    return template.replace("{{TRANSCRIPT}}", transcript).replace("{{LANG_HINT}}", lang_hint)


def get_summary_system_prompt(prompt_type: Optional[str] = None) -> str:
    effective_type = (prompt_type or SUMMARY_PROMPT_TYPE or "curated").strip()
    if effective_type == "classic":
        return "Sei un assistente che comprime conversazioni per un altro modello. Non inventare nulla. Sii fedele, compatto e utile."
    if effective_type == "structured":
        return "Sei un assistente che trasforma conversazioni in stato strutturato compatto. Non inventare nulla. Mantieni solo ciò che è utile a continuare la conversazione."
    return "You are a context compaction engine. Be faithful, compact, structured, and do not invent information."


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
        s = ""
        for m in msgs:
            c = m.get("content", "")
            if isinstance(c, str):
                s += c
        return max(1, int(len(s) / 4))


def _estimate_repacked_tokens(tok: TokenCounter, *, sys_msg: Optional[Dict[str, Any]], head: List[Dict[str, Any]], tail: List[Dict[str, Any]], summary_budget_tok: int) -> int:
    msgs: List[Dict[str, Any]] = []
    if sys_msg:
        msgs.append(sys_msg)
    msgs.extend(head)
    msgs.extend(tail)
    base = _estimate_tokens_for_msgs(tok, msgs)
    return base + summary_budget_tok + 16


def _choose_head_tail(tok: TokenCounter, *, sys_msg: Optional[Dict[str, Any]], non_system: List[Dict[str, Any]], threshold: int, max_head: int, max_tail: int, summary_budget_tok: int) -> Tuple[int, int, int]:
    n = len(non_system)
    if n <= 2:
        return (0, 0, _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=[], tail=[], summary_budget_tok=summary_budget_tok))
    for total in range(min(max_head + max_tail, n), 1, -1):
        head_max_this = min(max_head, total - 1)
        for head_n in range(head_max_this, -1, -1):
            tail_n = total - head_n
            if tail_n < 0 or tail_n > max_tail or head_n + tail_n >= n:
                continue
            middle_count = n - head_n - tail_n
            if middle_count <= 0:
                continue
            head = non_system[:head_n]
            tail = non_system[-tail_n:] if tail_n > 0 else []
            est = _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=head, tail=tail, summary_budget_tok=summary_budget_tok)
            if est <= threshold:
                return head_n, tail_n, est
    head_n = min(1, max_head, n - 1)
    tail_n = min(1, max_tail, n - head_n)
    if head_n + tail_n >= n:
        head_n = 0
        tail_n = min(1, max_tail, n - 1)
    head = non_system[:head_n] if head_n else []
    tail = non_system[-tail_n:] if tail_n else []
    est = _estimate_repacked_tokens(tok, sys_msg=sys_msg, head=head, tail=tail, summary_budget_tok=summary_budget_tok)
    return head_n, tail_n, est


def should_summarise(*, tok: TokenCounter, messages: List[Dict[str, Any]], ctx_eff: int, max_out: int, safety_margin_tok: int = SAFETY_MARGIN_TOK, max_head: int = MAX_HEAD, max_tail: int = MAX_TAIL, summary_insert_budget_tok: int = SUMMARY_INSERT_BUDGET_TOK) -> SummarizePlan:
    threshold = max(256, int(ctx_eff) - int(max_out) - int(safety_margin_tok))
    prompt_tok_est = _estimate_tokens_for_msgs(tok, messages)
    sys_msg, non_system = split_messages(messages)
    n = len(non_system)
    if prompt_tok_est <= threshold:
        return SummarizePlan(False, "prompt_within_threshold", threshold, prompt_tok_est, 0, 0, max(0, n), prompt_tok_est)
    if n < 3:
        return SummarizePlan(False, "too_few_messages", threshold, prompt_tok_est, 0, 0, max(0, n), prompt_tok_est)
    head_n, tail_n, repacked_est = _choose_head_tail(tok, sys_msg=sys_msg, non_system=non_system, threshold=threshold, max_head=max_head, max_tail=max_tail, summary_budget_tok=summary_insert_budget_tok)
    middle_count = max(0, n - head_n - tail_n)
    if middle_count <= 0:
        return SummarizePlan(False, "no_middle", threshold, prompt_tok_est, head_n, tail_n, 0, prompt_tok_est)
    log("INFO", "summary_plan", should=True, reason="prompt_exceeds_threshold", threshold=threshold, prompt_tok_est=prompt_tok_est, head_n=head_n, tail_n=tail_n, middle_count=middle_count, repacked_tok_est=repacked_est)
    return SummarizePlan(True, "prompt_exceeds_threshold", threshold, prompt_tok_est, head_n, tail_n, middle_count, repacked_est)


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


def _sanitize_summary_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "(Contesto compattato non disponibile.)"
    markers = [
        "=== TRANSCRIPT START ===",
        "=== TRANSCRIPT END ===",
        "=== EXISTING SUMMARY START ===",
        "=== EXISTING SUMMARY END ===",
        "=== NEW MESSAGES START ===",
        "=== NEW MESSAGES END ===",
    ]
    lines = [ln for ln in text.splitlines() if ln.strip() not in markers and not ln.strip().startswith("RISPOSTA:")]
    cleaned = "\n".join(lines).strip()
    return cleaned or "(Contesto compattato non disponibile.)"


def _build_summary_body(summary_model: str, sys: str, user: str) -> Dict[str, Any]:
    return {
        "model": summary_model,
        "messages": [
            {"role": "system", "content": sys},
            {"role": "user", "content": user},
        ],
        "temperature": SUMMARY_TEMPERATURE,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "stream": False,
    }


async def _run_summary_call(*, req_id: str, summary_model: str, sys: str, user: str, log_msg: str, meta: Dict[str, Any]) -> str:
    body = _build_summary_body(summary_model, sys, user)
    log("INFO", log_msg, req_id=req_id, summary_model=summary_model, body_json=snip_json(body), **meta)
    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    client = await http_client()
    t0 = time.time()
    r = await client.post(url, json=body)
    elapsed_ms = (time.time() - t0) * 1000.0
    r.raise_for_status()
    data = r.json()
    try:
        summary = data["choices"][0]["message"]["content"]
    except Exception:
        summary = ""
    summary = _sanitize_summary_text(summary)
    log("INFO", f"{log_msg}_reply", req_id=req_id, elapsed_ms=round(elapsed_ms, 2), usage=data.get("usage"), summary_chars=len(summary), summary_snip=snip_json(summary, max_chars=4000))
    return summary


def _chunk_messages_by_budget(messages: List[Dict[str, Any]], max_chunk_tokens: int, tok: Optional[TokenCounter] = None) -> List[List[Dict[str, Any]]]:
    tok = tok or TokenCounter()
    chunks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_tok = 0
    for m in messages:
        mtok = _estimate_tokens_for_msgs(tok, [m])
        if cur and cur_tok + mtok > max_chunk_tokens:
            chunks.append(cur)
            cur = []
            cur_tok = 0
        cur.append(m)
        cur_tok += mtok
    if cur:
        chunks.append(cur)
    return chunks or [messages]


async def summarize_middle(middle: List[Dict[str, Any]], req_id: str, summary_model: str, *, prompt_type: Optional[str] = None, lang_hint: str = "italiano") -> str:
    if not middle:
        return "(Contesto compattato non disponibile.)"

    tok = TokenCounter()
    summary_ctx = await get_ctx_len_for_model(summary_model)
    input_budget = max(256, int(summary_ctx) - int(SUMMARY_MAX_TOKENS) - int(SAFETY_MARGIN_TOK))

    transcript = render_messages_for_summary(middle)
    prompt_est = tok.count_text(transcript)
    if prompt_est <= input_budget:
        sys = get_summary_system_prompt(prompt_type=prompt_type)
        user = render_summary_prompt(transcript, prompt_type=prompt_type, lang_hint=lang_hint)
        return await _run_summary_call(req_id=req_id, summary_model=summary_model, sys=sys, user=user, log_msg="summary_req", meta={"summary_prompt_type": (prompt_type or SUMMARY_PROMPT_TYPE), "middle_count": len(middle), "transcript_chars": len(transcript)})

    chunks = _chunk_messages_by_budget(middle, max_chunk_tokens=max(128, input_budget // 2), tok=tok)
    partials: List[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        partials.append(await summarize_middle(chunk, req_id=req_id, summary_model=summary_model, prompt_type=prompt_type, lang_hint=lang_hint))
        if idx >= 128:
            break

    synthetic = [{"role": "assistant", "content": p} for p in partials]
    passes = 1
    while len(synthetic) > 1 and passes < SUMMARY_OVERFLOW_MAX_PASSES:
        synthetic_chunks = _chunk_messages_by_budget(synthetic, max_chunk_tokens=max(128, input_budget // 2), tok=tok)
        merged: List[Dict[str, Any]] = []
        for idx, chunk in enumerate(synthetic_chunks, start=1):
            merged_text = await summarize_middle(chunk, req_id=req_id, summary_model=summary_model, prompt_type=prompt_type, lang_hint=lang_hint)
            merged.append({"role": "assistant", "content": merged_text})
            if idx >= 128:
                break
        synthetic = merged
        passes += 1
    return _sanitize_summary_text(synthetic[0].get("content", "") if synthetic else "")


async def summarize_incremental(existing_summary: str, new_messages: List[Dict[str, Any]], req_id: str, summary_model: str) -> str:
    cleaned_existing = _sanitize_summary_text(existing_summary)
    if not new_messages:
        return cleaned_existing

    transcript = render_messages_for_summary(new_messages)
    tok = TokenCounter()
    summary_ctx = await get_ctx_len_for_model(summary_model)
    input_budget = max(256, int(summary_ctx) - int(SUMMARY_MAX_TOKENS) - int(SAFETY_MARGIN_TOK))
    est = tok.count_text(cleaned_existing) + tok.count_text(transcript)
    if est > input_budget:
        partial_new = await summarize_middle(new_messages, req_id=req_id, summary_model=summary_model)
        transcript = partial_new
    user = INCREMENTAL_SUMMARY_PROMPT.replace("{{EXISTING_SUMMARY}}", cleaned_existing).replace("{{TRANSCRIPT}}", transcript)
    return await _run_summary_call(req_id=req_id, summary_model=summary_model, sys="You update compact conversation state faithfully.", user=user, log_msg="summary_incremental_req", meta={"new_count": len(new_messages), "existing_chars": len(cleaned_existing), "transcript_chars": len(transcript)})


def _archived_block(summary_text: str) -> str:
    return (
        "[ARCHIVED_COMPACT_CONTEXT]\n"
        "The following block is a compressed reconstruction of earlier conversation content.\n"
        "Treat it as authoritative context for continuity, decisions, constraints, facts and pending work.\n"
        "Prefer this block over trying to infer missing older details from the recent tail alone.\n\n"
        f"{_sanitize_summary_text(summary_text)}\n"
        "[/ARCHIVED_COMPACT_CONTEXT]"
    )


def build_repacked_messages(original: List[Dict[str, Any]], *, summary_text: str, head_n: int, tail_n: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    sys_msg, non_system = split_messages(original)
    n = len(non_system)
    head_n = max(0, min(int(head_n), n))
    tail_n = max(0, min(int(tail_n), n - head_n))
    head = non_system[:head_n] if head_n else []
    tail = non_system[-tail_n:] if tail_n else []
    middle = non_system[head_n:n - tail_n] if (head_n + tail_n) < n else []
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
    repacked.append({"role": "system", "content": _archived_block(summary_text)})
    repacked.extend(tail)
    return repacked, middle


def build_checkpoint_repacked_messages(original: List[Dict[str, Any]], *, summary_text: str, covered_count: int, tail_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sys_msg, non_system = split_messages(original)
    repacked: List[Dict[str, Any]] = []
    if sys_msg:
        repacked.append(sys_msg)
    if covered_count > 0:
        repacked.append({"role": "system", "content": _archived_block(summary_text)})
    repacked.extend(tail_messages or non_system)
    return repacked
