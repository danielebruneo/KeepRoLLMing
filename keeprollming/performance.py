from __future__ import annotations

import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

PERFORMANCE_LOGS_DIR = os.getenv("PERFORMANCE_LOGS_DIR", "./__performance_logs")

_LOCK = threading.Lock()


def _ensure_dir() -> Path:
    path = Path(PERFORMANCE_LOGS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        x = float(value)
    except Exception:
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _safe_slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    s = s.strip("._-")
    return s or "unknown_model"


def _format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"

    text = str(value)
    if text == "" or any(ch in text for ch in ":#\n\r\t") or text.strip() != text:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _dump_yaml_list(items: Iterable[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for item in items:
        lines.append("-")
        for key, value in item.items():
            lines.append(f"  {key}: {_format_scalar(value)}")
    if not lines:
        return "[]\n"
    return "\n".join(lines) + "\n"


def _read_entries(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        if line == "-":
            if current is not None:
                entries.append(current)
            current = {}
            continue
        if current is None or not line.startswith("  ") or ":" not in line:
            continue

        key, raw_value = line.strip().split(":", 1)
        value = raw_value.strip()
        if value == "null":
            parsed: Any = None
        elif value in {"true", "false"}:
            parsed = value == "true"
        elif value.startswith('"') and value.endswith('"'):
            parsed = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        else:
            try:
                parsed = int(value)
            except Exception:
                try:
                    parsed = float(value)
                except Exception:
                    parsed = value
        current[key] = parsed

    if current is not None:
        entries.append(current)
    return entries


def _stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"avg": None, "min": None, "max": None}
    return {
        "avg": round(sum(values) / len(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def _update_summary(base_dir: Path) -> None:
    lines: List[str] = ["models:"]
    model_paths = sorted(base_dir.glob("*.requests.yaml"))

    if not model_paths:
        lines.append("  []")
        (base_dir / "summary.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    for path in model_paths:
        entries = _read_entries(path)
        if not entries:
            continue

        # Use route_name as primary identifier, fallback to model name
        model_name = str(entries[-1].get("route_name") or entries[-1].get("model") or path.stem.replace(".requests", "")).strip() or "unknown"

        # Collect all metrics
        tps_values = [v for v in (_safe_float(e.get("tps")) for e in entries) if v is not None]
        total_tps_values = [v for v in (_safe_float(e.get("total_tps")) for e in entries) if v is not None]
        ttft_values = [v for v in (_safe_float(e.get("ttft_ms")) for e in entries) if v is not None]
        completion_tokens_values = [v for v in (_safe_int(e.get("completion_tokens")) for e in entries) if v is not None]
        prompt_tokens_values = [v for v in (_safe_int(e.get("prompt_tokens")) for e in entries) if v is not None]
        completion_tps_values = [v for v in (_safe_float(e.get("completion_tps")) for e in entries) if v is not None]
        prompt_tps_values = [v for v in (_safe_float(e.get("prompt_tps")) for e in entries) if v is not None]

        tps_stats = _stats(tps_values)
        ttft_stats = _stats(ttft_values)
        completion_tokens_stats = _stats([float(v) for v in completion_tokens_values]) if completion_tokens_values else {"avg": None, "min": None, "max": None}
        prompt_tokens_stats = _stats([float(v) for v in prompt_tokens_values]) if prompt_tokens_values else {"avg": None, "min": None, "max": None}
        completion_tps_stats = _stats(completion_tps_values)
        prompt_tps_stats = _stats(prompt_tps_values)
        total_tps_stats = _stats(total_tps_values)

        lines.append("  -")
        lines.append(f"    route_name: {_format_scalar(model_name)}")
        lines.append(f"    model: {_format_scalar(entries[-1].get('model'))}")
        lines.append(f"    requests: {_format_scalar(len(entries))}")
        
        # TPS stats (overall, completion, prompt)
        lines.append("    tps:")
        lines.append(f"      avg: {_format_scalar(tps_stats['avg'])}")
        lines.append(f"      min: {_format_scalar(tps_stats['min'])}")
        lines.append(f"      max: {_format_scalar(tps_stats['max'])}")
        
        lines.append("    total_tps:")
        lines.append(f"      avg: {_format_scalar(total_tps_stats['avg'])}")
        lines.append(f"      min: {_format_scalar(total_tps_stats['min'])}")
        lines.append(f"      max: {_format_scalar(total_tps_stats['max'])}")

        lines.append("    completion_tps:")
        lines.append(f"      avg: {_format_scalar(completion_tps_stats['avg'])}")
        lines.append(f"      min: {_format_scalar(completion_tps_stats['min'])}")
        lines.append(f"      max: {_format_scalar(completion_tps_stats['max'])}")
        
        lines.append("    prompt_tps:")
        lines.append(f"      avg: {_format_scalar(prompt_tps_stats['avg'])}")
        lines.append(f"      min: {_format_scalar(prompt_tps_stats['min'])}")
        lines.append(f"      max: {_format_scalar(prompt_tps_stats['max'])}")
        
        # Token counts stats
        lines.append("    completion_tokens:")
        lines.append(f"      avg: {_format_scalar(completion_tokens_stats['avg'])}")
        lines.append(f"      min: {_format_scalar(completion_tokens_stats['min'])}")
        lines.append(f"      max: {_format_scalar(completion_tokens_stats['max'])}")
        
        lines.append("    prompt_tokens:")
        lines.append(f"      avg: {_format_scalar(prompt_tokens_stats['avg'])}")
        lines.append(f"      min: {_format_scalar(prompt_tokens_stats['min'])}")
        lines.append(f"      max: {_format_scalar(prompt_tokens_stats['max'])}")
        
        # TTFT stats
        lines.append("    ttft_ms:")
        lines.append(f"      avg: {_format_scalar(ttft_stats['avg'])}")
        lines.append(f"      min: {_format_scalar(ttft_stats['min'])}")
        lines.append(f"      max: {_format_scalar(ttft_stats['max'])}")
        
        lines.append(f"    updated_at: {_format_scalar(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))}")

    (base_dir / "summary.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def compute_request_performance(*, elapsed_ms: Any, completion_tokens: Any, ttft_ms: Any = None, prompt_tokens: Any = None) -> Dict[str, Any]:
    elapsed = _safe_float(elapsed_ms)
    completion = _safe_int(completion_tokens)
    ttft = _safe_float(ttft_ms)
    prompt = _safe_int(prompt_tokens)
    
    tps = None
    completion_tps = None
    prompt_tps = None

    # Keep TPS stable and comparable across buffered/cached/streamed responses.
    # Using (elapsed - TTFT) can explode to unrealistic values when the stream is
    # delivered in one burst or the final usage arrives almost immediately after
    # the first content token. TTFT is tracked separately.
    if elapsed is not None and elapsed > 0:
        if completion is not None and completion >= 0:
            # Completion TPS based on generation time (elapsed - TTFT)
            gen_time = elapsed - ttft if ttft is not None else elapsed
            if gen_time > 0:
                completion_tps = completion / (gen_time / 1000.0)
        
        if prompt is not None and prompt >= 0:
            # Prompt TPS based on TTFT if available, otherwise use elapsed time
            if ttft is not None and ttft > 0:
                # Use TTFT for prompt processing time (time before first token)
                prompt_tps = prompt / (ttft / 1000.0)
            else:
                prompt_tps = prompt / (elapsed / 1000.0)
        
        # Overall TPS is total tokens per second (standard metric for end-to-end throughput)
        if prompt is not None and completion is not None:
            total_tps = (prompt + completion) / (elapsed / 1000.0)
        else:
            total_tps = None
        
        # For backward compatibility, keep 'tps' as alias for completion_tps
        # New code should use 'total_tps' for overall throughput

    return {
        "elapsed_ms": round(elapsed, 4) if elapsed is not None else None,
        "completion_tokens": completion,
        "prompt_tokens": prompt,
        "ttft_ms": round(ttft, 4) if ttft is not None else None,
        "tps": round(completion_tps, 4) if completion_tps is not None else None,
        "total_tps": round(total_tps, 4) if total_tps is not None else None,
        "completion_tps": round(completion_tps, 4) if completion_tps is not None else None,
        "prompt_tps": round(prompt_tps, 4) if prompt_tps is not None else None,
    }


def record_request_performance(
    *,
    model: str,
    route_name: str | None = None,
    req_id: str,
    stream: bool,
    elapsed_ms: Any,
    completion_tokens: Any,
    ttft_ms: Any = None,
    prompt_tokens: Any = None,
    total_tokens: Any = None,
    finish_reason: Any = None,
    did_summarize: Any = None,
    passthrough: Any = None,
    completion_tokens_source: Any = None,
) -> Dict[str, Any]:
    base_dir = _ensure_dir()
    metrics = compute_request_performance(elapsed_ms=elapsed_ms, completion_tokens=completion_tokens, ttft_ms=ttft_ms, prompt_tokens=prompt_tokens)

    entry: Dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "req_id": req_id,
        "model": model,
        "route_name": route_name or _safe_slug(model),
        "stream": bool(stream),
        "elapsed_ms": metrics["elapsed_ms"],
        "ttft_ms": metrics["ttft_ms"],
        "tps": metrics["tps"],
        "total_tps": metrics["total_tps"],
        "completion_tps": metrics["completion_tps"],
        "prompt_tps": metrics["prompt_tps"],
        "completion_tokens": _safe_int(completion_tokens),
        "prompt_tokens": _safe_int(prompt_tokens),
        "total_tokens": _safe_int(total_tokens),
        "finish_reason": finish_reason,
        "completion_tokens_source": completion_tokens_source,
        "did_summarize": bool(did_summarize) if did_summarize is not None else None,
        "passthrough": bool(passthrough) if passthrough is not None else None,
    }

    path = base_dir / f"{_safe_slug(model)}.requests.yaml"
    with _LOCK:
        entries = _read_entries(path)
        entries.append(entry)
        path.write_text(_dump_yaml_list(entries), encoding="utf-8")
        _update_summary(base_dir)

    return entry
