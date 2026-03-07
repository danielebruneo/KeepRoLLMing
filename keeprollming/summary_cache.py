from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


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


def conversation_fingerprint(messages: List[Dict[str, Any]], n_head: int = 1) -> str:
    n = max(1, int(n_head or 1))
    return _digest(normalize_message_for_hash(m) for m in messages[:n])[:16]


def range_hash(messages: List[Dict[str, Any]], start_idx: int, end_idx: int) -> str:
    if start_idx < 0 or end_idx < start_idx:
        return _digest([])
    return _digest(normalize_message_for_hash(m) for m in messages[start_idx : end_idx + 1])


def ensure_cache_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_cache_filename(entry: SummaryCacheEntry) -> str:
    return f"{entry.fingerprint}__{entry.start_idx}_{entry.end_idx}__{entry.range_hash[:16]}.json"


def save_cache_entry(cache_dir: str | Path, entry: SummaryCacheEntry) -> Path:
    p = ensure_cache_dir(cache_dir) / build_cache_filename(entry)
    p.write_text(json.dumps(entry.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_cache_entries(cache_dir: str | Path, fingerprint: str) -> list[SummaryCacheEntry]:
    p = ensure_cache_dir(cache_dir)
    out: list[SummaryCacheEntry] = []
    for f in p.glob(f"{fingerprint}__*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(SummaryCacheEntry(**data))
        except Exception:
            continue
    return out


def find_best_prefix_entry(entries: List[SummaryCacheEntry], messages: List[Dict[str, Any]]) -> Optional[SummaryCacheEntry]:
    best: Optional[SummaryCacheEntry] = None
    for entry in entries:
        if entry.start_idx != 0 or entry.end_idx < 0:
            continue
        if entry.end_idx >= len(messages):
            continue
        if range_hash(messages, entry.start_idx, entry.end_idx) != entry.range_hash:
            continue
        if best is None or entry.end_idx > best.end_idx:
            best = entry
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
