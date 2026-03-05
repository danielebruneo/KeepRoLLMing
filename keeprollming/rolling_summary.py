from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .config import SUMMARY_MAX_TOKENS, UPSTREAM_BASE_URL
from .logger import log, snip_json
from .upstream import http_client


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


async def summarize_middle(middle: List[Dict[str, Any]], req_id: str, summary_model: str) -> str:
    transcript = render_messages_for_summary(middle)

    sys = (
        "Sei un assistente che produce un RIASSUNTO DI CONTESTO per un altro modello.\n"
        "Obiettivo: comprimere la parte centrale preservando fatti, nomi, decisioni, richieste, vincoli e TODO.\n"
        "Regole: non inventare; mantieni lingua coerente (preferisci italiano).\n"
        "Output: breve e denso; bullet points se utile; includi decisioni e TODO.\n"
    )
    user = (
        "Riassumi la parte centrale della conversazione qui sotto.\n\n"
        "=== TRANSCRIPT START ===\n"
        f"{transcript}\n"
        "=== TRANSCRIPT END ===\n\n"
        "RISPOSTA (solo riassunto):"
    )

    body = {
        "model": summary_model,
        "messages": [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        "temperature": 0.4,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "stream": False,
    }

    log(
        "INFO",
        "summary_req",
        req_id=req_id,
        summary_model=summary_model,
        middle_count=len(middle),
        transcript_chars=len(transcript),
        body_json=snip_json(body),
    )

    url = f"{UPSTREAM_BASE_URL}/v1/chat/completions"
    t0 = time.time()
    client = await http_client()
    r = await client.post(url, json=body)
    elapsed_ms = (time.time() - t0) * 1000.0
    r.raise_for_status()

    data = r.json()
    try:
        summary = data["choices"][0]["message"]["content"]
    except Exception:
        summary = ""

    summary = (summary or "").strip() or "(Riassunto non disponibile.)"
    log(
        "INFO",
        "summary_reply",
        req_id=req_id,
        elapsed_ms=round(elapsed_ms, 2),
        usage=data.get("usage"),
        summary_chars=len(summary),
        summary_snip=snip_json(summary, max_chars=min(20000000, 4000)),
        raw_json=snip_json(data),
    )
    return summary


def build_repacked_messages(original: List[Dict[str, Any]], summary_text: str) -> List[Dict[str, Any]]:
    sys_msg, non_system = split_messages(original)

    head = non_system[:2]
    tail = non_system[-2:] if len(non_system) > 2 else non_system[2:]
    middle = non_system[2:-2] if len(non_system) > 4 else []

    if not middle:
        merged: List[Dict[str, Any]] = []
        if sys_msg:
            merged.append(sys_msg)
        merged.extend(non_system)
        return merged

    sys_text = ""
    if sys_msg and isinstance(sys_msg.get("content"), str) and sys_msg["content"].strip():
        sys_text += sys_msg["content"].strip() + "\n\n"

    sum_text = summary_text.strip()

    repacked: List[Dict[str, Any]] = [{"role": "system", "content": sys_text.strip()}]
    repacked.extend(head)
    repacked.append({"role": "system", "content": "riassunto messaggi intermedi:\n{}".format(sum_text)})
    repacked.extend(tail)
    return repacked
