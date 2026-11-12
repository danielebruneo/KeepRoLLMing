"""Bounded resource-level promotion evidence; never reads request logs at runtime."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import OrderedDict, deque
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping

RETENTION_SECONDS = 7 * 86400
MAX_RESOURCES = 128
MAX_SAMPLES = 1024


def performance_identity(upstream_url: Any, model: Any) -> str | None:
    """Opaque URL+model identity; aliases share evidence, changed models do not."""
    if not isinstance(upstream_url, str) or not upstream_url.strip() or not isinstance(model, str) or not model:
        return None
    url = upstream_url.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return hashlib.sha256(json.dumps([url, model.strip()], separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class PromotionMetricsPolicy:
    recent_min_samples: int = 3
    history_min_samples: int = 6
    history_max_age_seconds: float = 86400.0
    max_history_cv: float = 0.5
    historical_margin_ratio: float = 0.20
    configured_margin_ratio: float = 0.35


def parse_metrics_policy(raw: Any) -> PromotionMetricsPolicy:
    if not isinstance(raw, Mapping):
        raise ValueError("promotion.metrics must be a mapping")
    defaults = PromotionMetricsPolicy()
    unknown = set(raw) - {f.name for f in fields(defaults)}
    if unknown:
        raise ValueError(f"unsupported promotion.metrics key(s): {', '.join(sorted(map(str, unknown)))}")
    values = {}
    for field in fields(defaults):
        value = raw.get(field.name, getattr(defaults, field.name))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"promotion.metrics.{field.name} must be finite and numeric")
        if field.name.endswith("min_samples"):
            if not isinstance(value, int) or not 1 <= value <= MAX_SAMPLES:
                raise ValueError(f"promotion.metrics.{field.name} must be an integer in [1, {MAX_SAMPLES}]")
        elif field.name.endswith("margin_ratio"):
            if not 0 <= value < 1:
                raise ValueError(f"promotion.metrics.{field.name} must be in [0, 1)")
        elif value < 0 or (field.name == "history_max_age_seconds" and value > RETENTION_SECONDS):
            raise ValueError(f"invalid promotion.metrics.{field.name}")
        values[field.name] = value
    return PromotionMetricsPolicy(**values)


def parse_performance_hint(raw: Any) -> dict[str, float] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or set(raw) - {"prompt_tps", "completion_tps", "enabled"}:
        raise ValueError("performance_hint accepts only prompt_tps, completion_tps and enabled")
    if not isinstance(raw.get("enabled", True), bool):
        raise ValueError("performance_hint.enabled must be boolean")
    rates = {}
    for key in ("prompt_tps", "completion_tps"):
        value = raw.get(key)
        if value is None and raw.get("enabled") is False:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"performance_hint.{key} must be finite and positive")
        rates[key] = float(value)
    return rates if raw.get("enabled", True) else None


class _Rates:
    """Rolling moments: constant lookup, amortized expiry, fixed storage bound."""

    def __init__(self) -> None:
        self.samples: deque[tuple[float, float, float]] = deque()
        self.sums = [0.0, 0.0]
        self.squares = [0.0, 0.0]

    def _remove(self) -> None:
        _, pp, decode = self.samples.popleft()
        for i, rate in enumerate((pp, decode)):
            self.sums[i] -= rate
            self.squares[i] -= rate * rate

    def prune(self, cutoff: float) -> None:
        while self.samples and self.samples[0][0] < cutoff:
            self._remove()
        if not self.samples:
            self.sums[:] = self.squares[:] = [0.0, 0.0]

    def add(self, sample: tuple[float, float, float]) -> None:
        if len(self.samples) == MAX_SAMPLES:
            self._remove()
        self.samples.append(sample)
        for i, rate in enumerate(sample[1:]):
            self.sums[i] += rate
            self.squares[i] += rate * rate

    def summary(self, now: float) -> dict[str, Any]:
        count = len(self.samples)
        means = [value / count for value in self.sums]
        cv = max(math.sqrt(max(0.0, square / count - mean * mean)) / mean for square, mean in zip(self.squares, means))
        return {
            "prompt_tps": means[0],
            "completion_tps": means[1],
            "samples": count,
            "age_seconds": max(0.0, now - self.samples[-1][0]),
            "cv": cv,
        }


class PromotionEstimates:
    """Caller supplies synchronization. Only validated successful observations enter."""

    def __init__(self) -> None:
        self._resources: OrderedDict[str, tuple[_Rates, _Rates]] = OrderedDict()
        self._loaded_until: dict[str, float] = {}

    def observe(self, identity: Any, at: Any, pp: Any, decode: Any, *, seed: bool = False) -> None:
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            return
        if any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
            for v in (at, pp, decode)
        ):
            return
        if seed and at <= self._loaded_until.get(identity, 0):
            return
        pair = self._resources.get(identity)
        if pair is None:
            pair = self._resources[identity] = (_Rates(), _Rates())
        # Startup log files may be out of chronological order. Load sorts records;
        # root-log seeding must not insert old samples behind newer moments.
        if pair[1].samples and at < pair[1].samples[-1][0]:
            if seed:
                # Startup-only merging across alias log files. Runtime
                # observations never sort or scan historical samples.
                rows = sorted((*pair[1].samples, (at, pp, decode)))[-MAX_SAMPLES:]
                rebuilt = (_Rates(), _Rates())
                latest = rows[-1][0]
                for row in rows:
                    for rates, window in zip(rebuilt, (3600, RETENTION_SECONDS)):
                        if row[0] >= latest - window:
                            rates.add(row)
                self._resources[identity] = rebuilt
            return
        self._resources.move_to_end(identity)
        while len(self._resources) > MAX_RESOURCES:
            removed, _ = self._resources.popitem(last=False)
            self._loaded_until.pop(removed, None)
        for rates, window in zip(pair, (3600, RETENTION_SECONDS)):
            rates.prune(at - window)
            rates.add((at, pp, decode))

    def estimate(self, identity: str | None, hint: Any, policy: PromotionMetricsPolicy, now: float) -> dict[str, Any]:
        reason = "insufficient_samples"
        pair = self._resources.get(identity) if identity else None
        if pair:
            recent, history = pair
            recent.prune(now - 3600)
            history.prune(now - RETENTION_SECONDS)
            if len(recent.samples) >= policy.recent_min_samples:
                return {**recent.summary(now), "source": "recent", "confidence": "high", "margin_ratio": 0.0}
            if len(history.samples) >= policy.history_min_samples:
                stats = history.summary(now)
                if stats["age_seconds"] > policy.history_max_age_seconds:
                    reason = "history_expired"
                elif stats["cv"] > policy.max_history_cv:
                    reason = "history_variable"
                else:
                    return {
                        **stats,
                        "source": "historical",
                        "confidence": "medium",
                        "margin_ratio": policy.historical_margin_ratio,
                    }
        configured = parse_performance_hint(hint)
        if configured and identity:
            return {
                **configured,
                "source": "configured",
                "confidence": "low",
                "samples": 0,
                "age_seconds": None,
                "margin_ratio": policy.configured_margin_ratio,
                "fallback_reason": reason,
            }
        return {
            "source": "unavailable",
            "confidence": "none",
            "samples": len(pair[1].samples) if pair else 0,
            "reason": reason if identity else "resource_identity_unavailable",
        }

    def save(self, path: Path, now: float) -> None:
        """Atomic bounded metadata snapshot, called outside request processing."""
        rows = {}
        for key, (_, history) in self._resources.items():
            history.prune(now - RETENTION_SECONDS)
            if history.samples:
                rows[key] = list(history.samples)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as stream:
                temporary = stream.name
                json.dump({"version": 1, "resources": rows}, stream, allow_nan=False)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, path: Path, now: float) -> None:
        if not path.exists():
            return
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("promotion snapshot exceeds 16 MiB")
        document = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or document.get("version") != 1
            or not isinstance(document.get("resources"), dict)
        ):
            raise ValueError("invalid promotion snapshot schema")
        resources = document["resources"]
        if len(resources) > MAX_RESOURCES:
            raise ValueError("too many promotion snapshot resources")
        # Validate into a temporary provider; corrupt snapshots cannot partly load.
        loaded = PromotionEstimates()
        for identity, samples in resources.items():
            if (
                not isinstance(identity, str)
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise ValueError("invalid promotion snapshot resource identity")
            if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
                raise ValueError("invalid promotion snapshot samples")
            for row in samples:
                if (
                    not isinstance(row, list)
                    or len(row) != 3
                    or any(
                        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                        for v in row
                    )
                ):
                    raise ValueError("invalid promotion snapshot observation")
            for at, pp, decode in sorted(samples):
                if now - RETENTION_SECONDS <= at <= now:
                    loaded.observe(identity, at, pp, decode)
        self._resources = loaded._resources
        self._loaded_until = {key: pair[1].samples[-1][0] for key, pair in self._resources.items()}


def route_estimate(
    provider: PromotionEstimates | None, route: Any, policy: PromotionMetricsPolicy, now: float
) -> dict[str, Any]:
    identity = performance_identity(getattr(route, "upstream_url", None), getattr(route, "model", None))
    return (provider if provider is not None else PromotionEstimates()).estimate(
        identity, getattr(route, "performance_hint", None), policy, now
    )
