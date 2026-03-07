from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import SUMMARY_CACHE_DIR, SUMMARY_CACHE_FINGERPRINT_MSGS


@dataclass(frozen=True)
class CacheScope:
    provider: str
    scope_key: str
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    fingerprint: Optional[str] = None


@dataclass(frozen=True)
class SummaryCacheEntry:
    provider: str
    scope_key: str
    end_idx: int
    prefix_hash: str
    summary_text: str
    summary_model: str
    created_at: float
    token_estimate: int
    source_mode: str
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    path: Optional[str] = None


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(s: str) -> str:
    return _SAFE_RE.sub("_", s).strip("_") or "unknown"


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                txt = item.get("text")
                if isinstance(txt, str) and txt.strip():
                    parts.append(txt.strip())
        return "\n".join(parts).strip()
    return ""


def normalize_message_for_hash(msg: Dict[str, Any]) -> str:
    role = str(msg.get("role") or "unknown")
    content = _flatten_content(msg.get("content"))
    return f"{role}:{content}"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def conversation_fingerprint(messages: List[Dict[str, Any]], n_head: int = SUMMARY_CACHE_FINGERPRINT_MSGS) -> str:
    head = messages[: max(1, int(n_head or 1))]
    payload = "\n".join(normalize_message_for_hash(m) for m in head)
    return _sha(payload)


def prefix_hash(messages: List[Dict[str, Any]], end_idx: int) -> str:
    if end_idx < 0:
        return _sha("")
    payload = "\n".join(normalize_message_for_hash(m) for m in messages[: end_idx + 1])
    return _sha(payload)


def resolve_cache_scope(headers: Dict[str, str], messages: List[Dict[str, Any]]) -> CacheScope:
    user_id = headers.get("x-librechat-user-id") or None
    conversation_id = headers.get("x-librechat-conversation-id") or None
    message_id = headers.get("x-librechat-message-id") or None
    parent_message_id = headers.get("x-librechat-parent-message-id") or None

    if conversation_id:
        scope_key = f"librechat::{user_id or 'anon'}::{conversation_id}"
        return CacheScope(
            provider="librechat",
            scope_key=scope_key,
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            parent_message_id=parent_message_id,
        )

    fp = conversation_fingerprint(messages)
    return CacheScope(provider="generic", scope_key=f"generic::{fp[:24]}", fingerprint=fp)


def _scope_dir(cache_dir: str, scope: CacheScope) -> Path:
    base = Path(cache_dir)
    if scope.provider == "librechat":
        return base / "librechat" / _safe(scope.user_id or "anon") / _safe(scope.conversation_id or "unknown")
    return base / "generic" / _safe(scope.fingerprint or scope.scope_key)


def load_cache_entries(cache_dir: str, scope: CacheScope) -> List[SummaryCacheEntry]:
    scope_dir = _scope_dir(cache_dir, scope)
    if not scope_dir.exists():
        return []

    out: List[SummaryCacheEntry] = []
    for p in sorted(scope_dir.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                SummaryCacheEntry(
                    provider=str(raw.get("provider") or scope.provider),
                    scope_key=str(raw.get("scope_key") or scope.scope_key),
                    end_idx=int(raw["end_idx"]),
                    prefix_hash=str(raw["prefix_hash"]),
                    summary_text=str(raw.get("summary_text") or ""),
                    summary_model=str(raw.get("summary_model") or ""),
                    created_at=float(raw.get("created_at") or 0.0),
                    token_estimate=int(raw.get("token_estimate") or 0),
                    source_mode=str(raw.get("source_mode") or "cache_append"),
                    user_id=raw.get("user_id"),
                    conversation_id=raw.get("conversation_id"),
                    message_id=raw.get("message_id"),
                    parent_message_id=raw.get("parent_message_id"),
                    path=str(p),
                )
            )
        except Exception:
            continue
    return out


def find_best_checkpoint(entries: Iterable[SummaryCacheEntry], messages: List[Dict[str, Any]]) -> Optional[SummaryCacheEntry]:
    best: Optional[SummaryCacheEntry] = None
    for entry in entries:
        if entry.end_idx < 0 or entry.end_idx >= len(messages):
            continue
        if prefix_hash(messages, entry.end_idx) != entry.prefix_hash:
            continue
        if best is None or entry.end_idx > best.end_idx:
            best = entry
    return best


def save_cache_entry(
    cache_dir: str,
    scope: CacheScope,
    messages: List[Dict[str, Any]],
    *,
    end_idx: int,
    summary_text: str,
    summary_model: str,
    token_estimate: int,
    source_mode: str,
) -> str:
    if end_idx < 0:
        raise ValueError("end_idx must be >= 0")

    scope_dir = _scope_dir(cache_dir, scope)
    scope_dir.mkdir(parents=True, exist_ok=True)
    pfx_hash = prefix_hash(messages, end_idx)
    stamp = int(time.time() * 1000)
    filename = f"checkpoint__{end_idx:06d}__{pfx_hash[:16]}__{stamp}.json"
    path = scope_dir / filename

    payload = {
        "provider": scope.provider,
        "scope_key": scope.scope_key,
        "user_id": scope.user_id,
        "conversation_id": scope.conversation_id,
        "message_id": scope.message_id,
        "parent_message_id": scope.parent_message_id,
        "end_idx": end_idx,
        "prefix_hash": pfx_hash,
        "summary_text": summary_text,
        "summary_model": summary_model,
        "created_at": time.time(),
        "token_estimate": int(token_estimate),
        "source_mode": source_mode,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
