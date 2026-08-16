"""Performance module — per-request performance data.

**Ownership boundary:**
- `performance.py` = per-request performance data (TPS, TTFT, elapsed time,
  token counts for logging).
- `metrics.py` = cost/load/retry accounting (consumes ExecutionUsage directly
  from the pipeline after stream completion).

Token counts in this module are for logging purposes only. Cost/load/retry
accounting is handled by `metrics.py` which consumes `ExecutionUsage` directly.

**Key invariants:**
- I2: performance.py owns per-request performance data (TPS, TTFT, elapsed
  time).
- I5: Token counts in metrics.py are derived from ExecutionUsage, not tracked
  independently.
"""

import json
import math
import yaml
import shutil
from pathlib import Path
from datetime import datetime
import time
from typing import Any, Dict, List, Optional


# ── Incremental statistics accumulator ─────────────────────────────

class _MetricAccum:
    """Running min/max/avg accumulator for a single numeric metric.

    O(1) per update — no data is stored beyond the running aggregates.
    """

    __slots__ = ("_sum", "_min", "_max", "_cnt")

    def __init__(self) -> None:
        self._sum: float = 0.0
        self._min: float = float("inf")
        self._max: float = float("-inf")
        self._cnt: int = 0

    def update(self, value: float) -> None:
        self._sum += value
        if value < self._min:
            self._min = value
        if value > self._max:
            self._max = value
        self._cnt += 1

    def stats(self) -> Dict[str, Optional[float]]:
        if self._cnt == 0:
            return {"avg": None, "min": None, "max": None}
        return {
            "avg": round(self._sum / self._cnt, 4),
            "min": round(self._min, 4),
            "max": round(self._max, 4),
        }


class RouteStats:
    """O(1) per-request aggregator for a single route's performance metrics.

    Populated incrementally by ``update()`` and dumped to a YAML-compatible
    dict by ``to_route_entry()``.  No disk I/O on the hot path.

    **Ownership boundary:**
    - performance.py = per-request performance data (TPS, TTFT, elapsed time,
      token counts for logging) + ExecutionUsage aggregates for dashboard display.
    - metrics.py = cost/load/retry accounting (consumes ExecutionUsage directly
      from the pipeline, independent of performance.py).
    """

    __slots__ = (
        "route_name", "model", "route_hierarchy",
        "count", "last_ts", "first_completed_at", "last_completed_at", "window_request_count",
        "total_tps", "completion_tps", "prompt_tps",
        "completion_tokens", "prompt_tokens", "cached_prompt_tokens", "uncached_prompt_tokens",
        "ttft_ms", "elapsed_ms",
        # ExecutionUsage accumulators (Phase 12)
        "upstream_attempts", "usage_reported_attempts",
        "recovery_count", "retry_amplification_ratio", "usage_complete_count",
    )

    def __init__(self, entry: Dict[str, Any]) -> None:
        self.route_name = str(entry.get("route_name") or "unknown")
        self.model = str(entry.get("model") or self.route_name)
        rh = entry.get("route_hierarchy")
        self.route_hierarchy: List[str] = rh if isinstance(rh, list) else [self.route_name]
        self.count = 1
        self.last_ts = time.time()
        completed_at = _safe_float(entry.get("completed_at"))
        self.first_completed_at = completed_at
        self.last_completed_at = completed_at
        self.window_request_count = 1 if completed_at is not None else 0
        self.total_tps = _MetricAccum()
        self.completion_tps = _MetricAccum()
        self.prompt_tps = _MetricAccum()
        self.completion_tokens = _MetricAccum()
        self.prompt_tokens = _MetricAccum()
        self.cached_prompt_tokens = _MetricAccum()
        self.uncached_prompt_tokens = _MetricAccum()
        self.ttft_ms = _MetricAccum()
        self.elapsed_ms = _MetricAccum()
        # ExecutionUsage accumulators (Phase 12)
        self.upstream_attempts = _MetricAccum()
        self.usage_reported_attempts = _MetricAccum()
        self.recovery_count = _MetricAccum()
        self.retry_amplification_ratio = _MetricAccum()
        self.usage_complete_count = 0  # Count of requests with usage_complete=True
        self._apply_entry(entry)

    def update(self, entry: Dict[str, Any]) -> None:
        """Incorporate a single request entry (O(1))."""
        self.count += 1
        self.last_ts = time.time()
        completed_at = _safe_float(entry.get("completed_at"))
        if completed_at is not None:
            if self.first_completed_at is None:
                self.first_completed_at = completed_at
            self.last_completed_at = completed_at
            self.window_request_count += 1
        # Update model name if it changed (rare, but handle gracefully)
        model = str(entry.get("model") or self.route_name)
        if model != self.model:
            self.model = model
        rh = entry.get("route_hierarchy")
        if isinstance(rh, list):
            self.route_hierarchy = rh
        self._apply_entry(entry)

    def _apply_entry(self, entry: Dict[str, Any]) -> None:
        # Performance metrics
        for name in ("total_tps", "completion_tps", "prompt_tps",
                     "completion_tokens", "prompt_tokens", "cached_prompt_tokens", "uncached_prompt_tokens",
                     "ttft_ms", "elapsed_ms"):
            v = _safe_float(entry.get(name))
            if v is not None:
                getattr(self, name).update(v)

        # ExecutionUsage metrics (Phase 12)
        upstream = _safe_int(entry.get("upstream_attempts"))
        if upstream is not None:
            self.upstream_attempts.update(float(upstream))

        reported = _safe_int(entry.get("usage_reported_attempts"))
        if reported is not None:
            self.usage_reported_attempts.update(float(reported))

        recovery = _safe_int(entry.get("recovery_count"))
        if recovery is not None:
            self.recovery_count.update(float(recovery))

        ratio = _safe_float(entry.get("retry_amplification_ratio"))
        if ratio is not None:
            self.retry_amplification_ratio.update(ratio)

        # Track usage_complete as a count (for computing percentage)
        if entry.get("usage_complete") is True:
            self.usage_complete_count += 1

    def to_route_entry(self) -> Dict[str, Any]:
        # Calculate usage_complete percentage
        usage_complete_pct = 0.0
        if self.count > 0:
            usage_complete_pct = self.usage_complete_count / self.count

        requests_per_hour = None
        if (self.first_completed_at is not None and self.last_completed_at is not None
                and self.last_completed_at > self.first_completed_at):
            requests_per_hour = self.window_request_count * 3600.0 / (
                self.last_completed_at - self.first_completed_at
            )

        return {
            "route_name": self.route_name,
            "model": self.model,
            "route_hierarchy": self.route_hierarchy,
            "requests": self.count,
            "total_tps": self.total_tps.stats(),
            "completion_tps": self.completion_tps.stats(),
            "prompt_tps": self.prompt_tps.stats(),
            "completion_tokens": self.completion_tokens.stats(),
            "prompt_tokens": self.prompt_tokens.stats(),
            "cached_prompt_tokens": self.cached_prompt_tokens.stats(),
            "uncached_prompt_tokens": self.uncached_prompt_tokens.stats(),
            "ttft_ms": self.ttft_ms.stats(),
            "elapsed_ms": self.elapsed_ms.stats(),
            # ExecutionUsage metrics (Phase 12)
            "upstream_attempts": self.upstream_attempts.stats(),
            "usage_reported_attempts": self.usage_reported_attempts.stats(),
            "recovery_count": self.recovery_count.stats(),
            "retry_amplification_ratio": self.retry_amplification_ratio.stats(),
            "usage_complete_pct": round(usage_complete_pct, 4),
            "requests_per_hour": round(requests_per_hour, 4) if requests_per_hour is not None else None,
            "window_started_at": self.first_completed_at,
            "window_ended_at": self.last_completed_at,
            "window_requests": self.window_request_count,
            "updated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }


# ── Module state ────────────────────────────────────────────────────

# Cache for the performance logs directory - set by app.py during startup
_PERF_LOGS_DIR: Optional[Path] = None

# Summary update throttling — _update_summary is called only every N requests
_summary_counter: int = 0
_summary_interval: int = 20

# In-memory route stats — O(1) aggregate per request, zero I/O for summary
_route_stats: Dict[str, RouteStats] = {}
_route_stats_seeded: bool = False


# ── Public API ──────────────────────────────────────────────────────

def set_performance_logs_dir(directory: str) -> None:
    """Set the directory for performance logs (can be changed at runtime)."""
    global _PERF_LOGS_DIR
    _PERF_LOGS_DIR = Path(directory)


def set_summary_interval(interval: int) -> None:
    """Set how often _update_summary runs (every N requests). Default: 100."""
    global _summary_interval
    _summary_interval = max(1, interval)


def reset_route_stats() -> None:
    """Clear all in-memory route stats and re-seed flag.

    Called after archive to start a fresh aggregation epoch.
    """
    global _route_stats_seeded
    _route_stats.clear()
    _route_stats_seeded = False


# ── Helpers ─────────────────────────────────────────────────────────

def _ensure_dir() -> Path:
    if _PERF_LOGS_DIR is not None:
        base_dir = _PERF_LOGS_DIR
    else:
        base_dir = Path(__file__).parent.parent / "__performance_logs"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        result = float(v)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _safe_slug(s: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(s))[:50]


def _read_entries(path: Path) -> List[Dict[str, Any]]:
    """Read entries from a JSON-lines file (one JSON object per line)."""
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    entries.append(obj)
            except json.JSONDecodeError:
                continue
    except Exception:
        return []
    return entries


def _seed_route_stats(base_dir: Path) -> None:
    """Pre-populate ``_route_stats`` from existing ``.requests.jsonl`` on disk.

    Runs once at first summary request after startup or archive.
    Clears any entries already added by the hot path so the file on disk is
    the canonical source of truth for the current epoch.
    """
    global _route_stats_seeded
    if _route_stats_seeded:
        return
    _route_stats.clear()
    for path in sorted(base_dir.glob("*.requests.jsonl")):
        for entry in _read_entries(path):
            rn = str(entry.get("route_name") or "unknown")
            if rn in _route_stats:
                _route_stats[rn].update(entry)
            else:
                _route_stats[rn] = RouteStats(entry)
    _route_stats_seeded = True


def _append_entry(path: Path, record: Dict[str, Any]) -> None:
    """Append a single JSON line to a file (atomic append).

    Uses the AsyncLogWriter when available to avoid blocking the event loop.
    Falls back to synchronous open()+write() when the writer is not running or
    when the target sink has been marked dead due to I/O errors (BUG 2 fix).
    """
    try:
        from .async_log_writer import get_async_writer
        writer = get_async_writer()
        sink_name = f"perf_{path.name}"
        if writer._running and not writer.is_sink_dead(sink_name):
            if sink_name not in writer._sinks:
                writer.register_sink(sink_name, str(path))
            writer.enqueue(sink_name, record)
            return
    except Exception:
        pass

    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


# ── Summary (now incremental, O(routes) I/O instead of O(entries)) ─

def _update_summary(base_dir: Path) -> None:
    """Write ``summary.yaml`` from in-memory route statistics.

    Reads no ``.requests.jsonl`` files on the hot path — all aggregates are
    maintained incrementally in ``_route_stats``.
    """
    _seed_route_stats(base_dir)

    if not _route_stats:
        with open(base_dir / "summary.yaml", 'w', encoding='utf-8') as f:
            yaml.dump({"models": []}, f)
        return

    model_to_routes: Dict[str, List[Dict[str, Any]]] = {}
    for stats in _route_stats.values():
        model = stats.model
        if model not in model_to_routes:
            model_to_routes[model] = []
        model_to_routes[model].append(stats.to_route_entry())

    models_list = []
    for model in sorted(model_to_routes.keys()):
        routes = model_to_routes[model]
        routes.sort(key=lambda r: r.get("requests", 0), reverse=True)
        models_list.append({"model": model, "routes": routes})

    summary_data = {
        "updated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "models": models_list,
    }
    with open(base_dir / "summary.yaml", 'w', encoding='utf-8') as f:
        yaml.dump(summary_data, f, allow_unicode=True, sort_keys=False)


# ── Performance computation ─────────────────────────────────────────

def compute_request_performance(
    *, elapsed_ms: Any, completion_tokens: Any, ttft_ms: Any = None,
    prompt_tokens: Any = None, total_tokens: Any = None,
    cached_prompt_tokens: Any = None,
) -> Dict[str, Any]:
    """Compute physical throughput while retaining logical token counts.

    ``prompt_tokens`` and ``total_tokens`` describe the client-visible logical
    request.  When an upstream reports KV-cache hits, only the uncached part of
    the prompt consumed prefill time; ``prompt_tps`` and ``total_tps`` must use
    that physical workload rather than the full logical context.
    """
    elapsed = _safe_float(elapsed_ms)
    completion = _safe_int(completion_tokens)
    ttft = _safe_float(ttft_ms)
    prompt = _safe_int(prompt_tokens)
    cached_prompt = _safe_int(cached_prompt_tokens)

    # Providers occasionally report an invalid cache count.  Keep token
    # accounting robust and never turn a malformed value into negative TPS.
    if cached_prompt is not None:
        cached_prompt = max(0, cached_prompt)
        if prompt is not None:
            cached_prompt = min(cached_prompt, prompt)
    uncached_prompt = (
        prompt - (cached_prompt or 0)
        if prompt is not None else None
    )

    # Calculate total if not provided
    if total_tokens is None and prompt is not None and completion is not None:
        total_tokens = prompt + completion

    total = _safe_int(total_tokens)

    completion_tps = None
    prompt_tps = None
    total_tps = None

    if elapsed is not None and elapsed > 0:
        if completion is not None and completion >= 0:
            gen_time = elapsed - ttft if ttft is not None else elapsed
            if gen_time > 0:
                completion_tps = completion / (gen_time / 1000.0)

        if uncached_prompt is not None and uncached_prompt >= 0:
            if ttft is not None and ttft > 0:
                prompt_tps = uncached_prompt / (ttft / 1000.0)
            else:
                prompt_tps = uncached_prompt / (elapsed / 1000.0)

        if uncached_prompt is not None and completion is not None:
            total_tps = (uncached_prompt + completion) / (elapsed / 1000.0)
        else:
            total_tps = None

    return {
        "elapsed_ms": round(elapsed, 4) if elapsed is not None else None,
        "completion_tokens": completion,
        "prompt_tokens": prompt,
        "cached_prompt_tokens": cached_prompt,
        "uncached_prompt_tokens": uncached_prompt,
        "total_tokens": total,
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
    route_hierarchy: List[str] | None = None,
    req_id: str,
    stream: bool,
    elapsed_ms: Any,
    completion_tokens: Any,
    ttft_ms: Any = None,
    prompt_tokens: Any = None,
    total_tokens: Any = None,
    cached_prompt_tokens: Any = None,
    finish_reason: Any = None,
    did_summarize: Any = None,
    passthrough: Any = None,
    completion_tokens_source: Any = None,
    performance_logs_dir: str | None = None,
    execution_usage: Any = None,
) -> Dict[str, Any]:
    """Record request performance metrics.

    Every call:
    - Computes derived metrics (TPS, etc.) — O(1)
    - Appends one JSON line to the per-route ``.requests.jsonl`` file — O(1) I/O
    - Updates the in-memory ``RouteStats`` aggregate — O(1)

    Every ``summary_update_interval`` requests:
    - Writes ``summary.yaml`` from in-memory state — O(routes) I/O only

    Every 1000 requests:
    - Archives ``.requests.jsonl`` files to ``archive/``
    - Resets in-memory stats for the new epoch
    """
    global _summary_counter
    if performance_logs_dir is not None:
        base_dir = Path(performance_logs_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
    else:
        base_dir = _ensure_dir()

    if cached_prompt_tokens is None and execution_usage is not None:
        cached_prompt_tokens = getattr(
            execution_usage, "final_cached_prompt_tokens", None
        )

    perf = compute_request_performance(
        elapsed_ms=elapsed_ms,
        completion_tokens=completion_tokens,
        ttft_ms=ttft_ms,
        prompt_tokens=prompt_tokens,
        total_tokens=total_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
    )

    record: Dict[str, Any] = {
        "model": model,
        "route_name": route_name or "unknown",
        "req_id": req_id,
        "stream": stream,
        **perf,
        "finish_reason": finish_reason,
        "did_summarize": did_summarize,
        "passthrough": passthrough,
        "completion_tokens_source": completion_tokens_source,
        "completed_at": time.time(),
    }

    if route_hierarchy is not None:
        record["route_hierarchy"] = route_hierarchy

    # ── Extract ExecutionUsage fields (Phase 12) ───────────────────
    if execution_usage is not None:
        # execution_usage is an ExecutionUsage dataclass
        record["upstream_attempts"] = getattr(execution_usage, 'upstream_attempts', 0)
        record["usage_reported_attempts"] = getattr(execution_usage, 'usage_reported_attempts', 0)
        record["recovery_count"] = getattr(execution_usage, 'recovery_count', 0)
        record["retry_amplification_ratio"] = _safe_float(
            getattr(execution_usage, 'retry_amplification_ratio', None)
        )
        record["usage_complete"] = getattr(execution_usage, 'usage_complete', False)
        record["upstream_prompt_tokens"] = getattr(
            execution_usage, 'upstream_prompt_tokens', None
        )
        record["upstream_completion_tokens"] = getattr(
            execution_usage, 'upstream_completion_tokens', None
        )
        record["upstream_total_tokens"] = getattr(
            execution_usage, 'upstream_total_tokens', None
        )

    # ── Seed in-memory stats from disk BEFORE any async I/O ──
    # This ensures existing data is loaded even if the async writer hasn't
    # flushed the current record yet (fixes empty summary on restart).
    _seed_route_stats(base_dir)

    # ── Append to JSONL on disk (via async writer) ──
    filename = f"{_safe_slug(route_name or 'unknown')}.requests.jsonl"
    path = base_dir / filename
    _append_entry(path, record)

    # ── Update in-memory stats (O(1)) ──
    rn = route_name or "unknown"
    if rn in _route_stats:
        _route_stats[rn].update(record)
    else:
        _route_stats[rn] = RouteStats(record)

    # ── Periodic summary flush and archive ──
    _summary_counter += 1
    if _summary_counter == 1 or _summary_counter % _summary_interval == 0:
        if _summary_counter > 1 and _summary_counter % 1000 == 0:
            archive_dir = base_dir / "archive"
            archive_dir.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            summary_path = base_dir / "summary.yaml"
            if summary_path.exists():
                archived = archive_dir / f"summary_{ts}.yaml"
                shutil.move(str(summary_path), str(archived))
            for f in base_dir.glob("*.requests.jsonl"):
                archived_file = archive_dir / f"{f.stem}_{ts}{f.suffix}"
                shutil.move(str(f), str(archived_file))
            reset_route_stats()
        _update_summary(base_dir)

    return perf


def get_performance_summary() -> Dict[str, Any]:
    """Return the summary data for the dashboard."""
    base_dir = _ensure_dir()
    summary_path = base_dir / "summary.yaml"

    if not summary_path.exists():
        return {"models": []}

    try:
        content = summary_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            return data
        elif isinstance(data, list):
            return {"models": data}
        return {"models": []}
    except Exception:
        return {"models": []}
