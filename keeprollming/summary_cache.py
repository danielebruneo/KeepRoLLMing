from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class SummaryCacheEntry:
    fingerprint: str
    start_idx: int
    end_idx: int
    range_hash: str
    summary_text: str
    summary_model: str
    created_at: float
    message_count: int
    token_estimate: int
    source_mode: str = "cache_append"


def _normalize_text(text: Any) -> str:
    if isinstance(text, str):
        out = text
    elif isinstance(text, list):
        parts: list[str] = []
        for item in text:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    parts.append(t)
        out = "\n".join(parts)
    else:
        out = ""
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"\s+", " ", out).strip()
    return out


def normalize_message_for_hash(msg: Dict[str, Any]) -> str:
    role = str(msg.get("role", ""))
    name = str(msg.get("name", ""))
    content = _normalize_text(msg.get("content"))
    return f"{role}|{name}|{content}"


def _digest(parts: Iterable[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()


def _safe_path_part(value: str, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    return safe or fallback


def conversation_fingerprint(
    messages: List[Dict[str, Any]] | None = None,
    n_head: int = 1,
    *,
    user_id: str = "",
    conv_id: str = "",
) -> str:
    """Generate a fingerprint based on provided context or messages.

    Supports the legacy positional form conversation_fingerprint(messages, n_head)
    while preferring LibreChat ids when available.
    """
    if user_id or conv_id:
        return _digest([
            normalize_message_for_hash({"role": "", "content": user_id}),
            normalize_message_for_hash({"role": "", "content": conv_id}),
        ])

    n = max(1, int(n_head or 1))
    parts: list[str] = []
    if messages is not None:
        for m in messages[:n]:
            parts.append(normalize_message_for_hash(m))
    return _digest(parts)[:16]


def range_hash(messages: List[Dict[str, Any]], start_idx: int, end_idx: int) -> str:
    if start_idx < 0 or end_idx < start_idx:
        return _digest([])
    return _digest(normalize_message_for_hash(m) for m in messages[start_idx : end_idx + 1])


def ensure_cache_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_cache_dir(cache_dir: str | Path, *, user_id: str = "", conv_id: str = "", fingerprint: str = "") -> Path:
    root = ensure_cache_dir(cache_dir)
    if user_id or conv_id:
        return ensure_cache_dir(root / "librechat" / _safe_path_part(user_id, "anonymous") / _safe_path_part(conv_id, fingerprint[:16] or "conversation"))
    return ensure_cache_dir(root / "generic" / _safe_path_part(fingerprint[:32], "default"))


def build_cache_filename(entry: SummaryCacheEntry) -> str:
    return f"{entry.start_idx:06d}_{entry.end_idx:06d}__{entry.range_hash[:16]}.json"


def save_cache_entry(cache_dir: str | Path, entry: SummaryCacheEntry, *, user_id: str = "", conv_id: str = "") -> Path:
    p = resolve_cache_dir(cache_dir, user_id=user_id, conv_id=conv_id, fingerprint=entry.fingerprint) / build_cache_filename(entry)
    p.write_text(json.dumps(entry.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_cache_entries(cache_dir: str | Path, fingerprint: str, *, user_id: str = "", conv_id: str = "") -> list[SummaryCacheEntry]:
    p = resolve_cache_dir(cache_dir, user_id=user_id, conv_id=conv_id, fingerprint=fingerprint)
    out: list[SummaryCacheEntry] = []
    for f in p.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("fingerprint") != fingerprint:
                continue
            out.append(SummaryCacheEntry(**data))
        except Exception:
            continue
    return out


def find_best_prefix_entry(entries: List[SummaryCacheEntry], messages: List[Dict[str, Any]], expected_start_idx: int = 0) -> Optional[SummaryCacheEntry]:
    best, _reasons = find_best_prefix_entry_with_reasons(entries, messages, expected_start_idx=expected_start_idx)
    return best


def make_cache_entry(
    *,
    fingerprint: str,
    start_idx: int,
    end_idx: int,
    messages: List[Dict[str, Any]],
    summary_text: str,
    summary_model: str,
    token_estimate: int,
    source_mode: str,
) -> SummaryCacheEntry:
    return SummaryCacheEntry(
        fingerprint=fingerprint,
        start_idx=start_idx,
        end_idx=end_idx,
        range_hash=range_hash(messages, start_idx, end_idx),
        summary_text=summary_text,
        summary_model=summary_model,
        created_at=time.time(),
        message_count=max(0, end_idx - start_idx + 1),
        token_estimate=token_estimate,
        source_mode=source_mode,
    )


def find_best_prefix_entry_with_reasons(
    entries: List[SummaryCacheEntry],
    messages: List[Dict[str, Any]],
    expected_start_idx: int = 0,
) -> Tuple[Optional[SummaryCacheEntry], List[Dict[str, Any]]]:
    best: Optional[SummaryCacheEntry] = None
    reasons: List[Dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: (e.start_idx, e.end_idx)):
        reason = None
        if entry.start_idx != expected_start_idx or entry.end_idx < entry.start_idx:
            reason = "start_mismatch"
        elif entry.end_idx >= len(messages):
            reason = "end_out_of_bounds"
        else:
            actual_hash = range_hash(messages, entry.start_idx, entry.end_idx)
            if actual_hash != entry.range_hash:
                reason = "hash_mismatch"
        if reason is not None:
            reasons.append({"start_idx": entry.start_idx, "end_idx": entry.end_idx, "reason": reason})
            continue
        if best is None or entry.end_idx > best.end_idx:
            best = entry
    return best, reasons
