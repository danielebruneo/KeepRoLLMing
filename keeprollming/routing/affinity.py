"""Bounded, privacy-preserving process-local session affinity.

Assignments are intentionally hints for route selection, not durable user
identity or authorization.  They retain only HMAC-derived opaque keys and the
chosen concrete route, and expire automatically.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# Assignment memory and resource reservations deliberately have distinct
# lifetimes.  Remembering a conversation for an hour lets a later turn find a
# known-warm target; holding a resource is a much shorter, separately managed
# soft lease.
DEFAULT_AFFINITY_TTL_SECONDS = 60 * 60
DEFAULT_RESERVATION_MIN_SECONDS = 60.0
DEFAULT_RESERVATION_MAX_SECONDS = 15 * 60.0
DEFAULT_RESERVATION_MIN_CONTEXT_RATIO = 0.10
DEFAULT_RESERVATION_MAX_CONTEXT_RATIO = 0.75
DEFAULT_AFFINITY_MAX_ENTRIES = 4096
_PREFIX_VERSION = b"krm-affinity-v1\x00"


@dataclass(frozen=True)
class AffinityLookup:
    """Result of looking up an opaque affinity key set."""

    route_name: str | None
    state: str
    cached_prompt_tokens: int | None = None
    owner_key: str | None = None


@dataclass(frozen=True)
class ReservationLookup:
    """Whether one resource may serve a session at this selection boundary."""

    state: str
    expires_in_seconds: float | None = None
    mode: str | None = None
    routing_priority: int | None = None
    context_ratio: float | None = None


@dataclass(frozen=True)
class CompactionHandoff:
    """One-shot opaque pin candidate inferred from a likely agent compaction."""

    route_name: str
    resource_key: str
    owner_key: str
    previous_prompt_tokens: int
    completion_tokens: int
    context_ratio: float


@dataclass
class _Assignment:
    route_name: str
    expires_at: float
    touched_at: float
    cached_prompt_tokens: int | None = None
    owner_key: str = ""


@dataclass
class _Reservation:
    owner_key: str
    route_name: str
    routing_priority: int
    mode: str
    expires_at: float
    context_ratio: float


@dataclass
class _CompactionCandidate:
    target_route_name: str
    resource_key: str
    owner_key: str
    prompt_tokens: int
    completion_tokens: int
    context_ratio: float
    expires_at: float


@dataclass
class _CompactionContinuation:
    target_route_name: str
    resource_key: str
    owner_key: str
    expires_at: float


def _canonical_bytes(value: Any) -> bytes:
    """Produce deterministic bytes without retaining the source payload."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class SessionAffinityRegistry:
    """Thread-safe, bounded assignment registry for one KRM process."""

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        ttl_seconds: float = DEFAULT_AFFINITY_TTL_SECONDS,
        max_entries: int = DEFAULT_AFFINITY_MAX_ENTRIES,
        clock: Any = time.monotonic,
    ) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._clock = clock
        self._lock = threading.RLock()
        self._assignments: dict[str, _Assignment] = {}
        self._reservations: dict[str, _Reservation] = {}
        self._compaction_candidates: dict[tuple[str, str], list[_CompactionCandidate]] = {}
        self._compaction_continuations: dict[str, _CompactionContinuation] = {}

    def digest(self, *, route_name: str, kind: str, value: Any) -> str:
        """Return an opaque, versioned HMAC key scoped to a requested route."""
        payload = (
            _PREFIX_VERSION
            + route_name.encode("utf-8")
            + b"\x00"
            + kind.encode("ascii")
            + b"\x00"
            + _canonical_bytes(value)
        )
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def candidate_keys(
        self,
        *,
        route_name: str,
        headers: Mapping[str, Any] | None,
        payload: Mapping[str, Any] | None,
        max_prefixes: int = 128,
    ) -> tuple[str, ...]:
        """Return strongest identity first, then full-message prefix digests.

        A later turn normally contains all earlier messages. Computing bounded
        message-boundary digests lets the registry find a previous full prompt
        without retaining raw conversation content. It is intentionally a
        conservative affinity signal; token-level cache confirmation is a
        separate later stage.
        """
        keys: list[str] = []
        headers = headers or {}
        # A routing assignment is not authorization, but a configured client
        # credential is still a useful tenant boundary. Scope every
        # conversation/prefix key by an opaque HMAC of it so two authenticated
        # clients with identical prompts cannot influence one another's pin.
        credential = headers.get("authorization") or headers.get("x-api-key")
        scope = self.digest(
            route_name=route_name,
            kind="credential-scope",
            value=credential.strip() if isinstance(credential, str) else "anonymous",
        )
        conversation_id = headers.get("x-librechat-conversation-id")
        if isinstance(conversation_id, str) and conversation_id.strip():
            keys.append(
                self.digest(
                    route_name=route_name,
                    kind="client-conversation",
                    value={"scope": scope, "id": conversation_id.strip()},
                )
            )

        messages = (payload or {}).get("messages")
        if not isinstance(messages, list):
            return tuple(keys)
        # Full prompt first, then progressively older message-boundary prefixes.
        # The cap prevents pathological long histories from creating an
        # unbounded CPU cost before routing.
        lower = max(1, len(messages) - max(1, int(max_prefixes)) + 1)
        for count in range(len(messages), lower - 1, -1):
            keys.append(
                self.digest(
                    route_name=route_name,
                    kind="message-prefix",
                    value={"scope": scope, "messages": messages[:count]},
                )
            )
        return tuple(keys)

    def assignment_keys(
        self,
        *,
        route_name: str,
        headers: Mapping[str, Any] | None,
        payload: Mapping[str, Any] | None,
    ) -> tuple[str, ...]:
        """Return identity plus the current full prompt key for assignment."""
        keys = self.candidate_keys(
            route_name=route_name,
            headers=headers,
            payload=payload,
            max_prefixes=1,
        )
        return tuple(dict.fromkeys(keys))

    def lookup(
        self,
        keys: Iterable[str],
        *,
        allowed_routes: Iterable[str],
    ) -> AffinityLookup:
        """Return a live assignment only when it remains an eligible target."""
        now = self._clock()
        allowed = set(allowed_routes)
        with self._lock:
            self._prune(now)
            for key in keys:
                assignment = self._assignments.get(key)
                if assignment is None:
                    continue
                assignment.touched_at = now
                if assignment.route_name in allowed:
                    state = "cache_hit" if (assignment.cached_prompt_tokens or 0) > 0 else "hit"
                    return AffinityLookup(
                        assignment.route_name,
                        state,
                        assignment.cached_prompt_tokens,
                        assignment.owner_key,
                    )
                return AffinityLookup(None, "ineligible")
        return AffinityLookup(None, "miss")

    def assign(
        self,
        keys: Iterable[str],
        *,
        route_name: str,
        ttl_seconds: float | None = None,
        owner_key: str | None = None,
    ) -> str | None:
        """Store an assignment and return its stable opaque owner key."""
        now = self._clock()
        unique_keys = tuple(dict.fromkeys(keys))
        if not unique_keys:
            return None
        effective_owner = owner_key or unique_keys[0]
        ttl = self._ttl_seconds if ttl_seconds is None else max(1.0, float(ttl_seconds))
        with self._lock:
            self._prune(now)
            assignment = _Assignment(
                route_name=route_name,
                expires_at=now + ttl,
                touched_at=now,
                owner_key=effective_owner,
            )
            for key in unique_keys:
                self._assignments[key] = assignment
            self._prune(now)
        return effective_owner

    def forget_owner_assignments(self, owner_key: str | None) -> int:
        """Remove stale prefix keys when an inferred compaction changes the prompt."""
        if not owner_key:
            return 0
        with self._lock:
            stale = [key for key, value in self._assignments.items() if value.owner_key == owner_key]
            for key in stale:
                del self._assignments[key]
            return len(stale)

    def record_compaction_candidate(
        self,
        *,
        source_route_name: str,
        source_scope_key: str,
        target_route_name: str,
        resource_key: str | None,
        owner_key: str | None,
        prompt_tokens: Any,
        completion_tokens: Any,
        ctx_len: Any,
        max_age_seconds: float,
        min_context_ratio: float,
        min_completion_tokens: int,
    ) -> str:
        """Remember only qualifying aggregate facts; never retain prompt content."""
        if not resource_key or not owner_key:
            return "identity_unavailable"
        try:
            prompt = int(prompt_tokens)
            completion = int(completion_tokens)
            context = int(ctx_len)
        except (TypeError, ValueError):
            return "usage_unavailable"
        if prompt <= 0 or context <= 0:
            return "context_unavailable"
        ratio = min(1.0, prompt / context)
        if ratio < min_context_ratio:
            return "below_context_threshold"
        if completion < min_completion_tokens:
            return "below_decode_threshold"
        now = self._clock()
        candidate = _CompactionCandidate(
            target_route_name=target_route_name,
            resource_key=resource_key,
            owner_key=owner_key,
            prompt_tokens=prompt,
            completion_tokens=completion,
            context_ratio=ratio,
            expires_at=now + max(0.1, float(max_age_seconds)),
        )
        with self._lock:
            self._prune(now)
            pending = self._compaction_candidates.setdefault((source_route_name, source_scope_key), [])
            pending.append(candidate)
            del pending[:-32]
        return "candidate_recorded"

    def consume_compaction_candidate(
        self,
        *,
        source_route_name: str,
        source_scope_key: str,
        new_prompt_tokens: Any,
        allowed_routes: Iterable[str],
        min_prompt_reduction_ratio: float,
    ) -> CompactionHandoff | None:
        """Claim the newest compatible recent candidate exactly once."""
        try:
            new_prompt = max(0, int(new_prompt_tokens))
        except (TypeError, ValueError):
            return None
        allowed = set(allowed_routes)
        now = self._clock()
        with self._lock:
            self._prune(now)
            key = (source_route_name, source_scope_key)
            pending = self._compaction_candidates.get(key, [])
            matching = [
                (index, candidate)
                for index, candidate in enumerate(pending)
                if candidate.target_route_name in allowed
                and candidate.prompt_tokens > 0
                and (candidate.prompt_tokens - new_prompt) / candidate.prompt_tokens >= min_prompt_reduction_ratio
            ]
            if not matching:
                return None
            index, candidate = max(matching, key=lambda pair: pair[1].expires_at)
            del pending[index]
            if not pending:
                self._compaction_candidates.pop(key, None)
            return CompactionHandoff(
                route_name=candidate.target_route_name,
                resource_key=candidate.resource_key,
                owner_key=candidate.owner_key,
                previous_prompt_tokens=candidate.prompt_tokens,
                completion_tokens=candidate.completion_tokens,
                context_ratio=candidate.context_ratio,
            )

    def _compaction_system_key(
        self,
        *,
        source_route_name: str,
        source_scope_key: str,
        payload: Mapping[str, Any] | None,
    ) -> str | None:
        messages = (payload or {}).get("messages")
        if not isinstance(messages, list):
            return None
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "system":
                continue
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                )
            if not isinstance(content, str) or not content:
                return None
            return self.digest(
                route_name=source_route_name,
                kind="compaction-system-continuation",
                value={"scope": source_scope_key, "head": content[:1024], "tail": content[-1024:]},
            )
        return None

    def record_compaction_continuation(
        self,
        *,
        source_route_name: str,
        source_scope_key: str,
        payload: Mapping[str, Any] | None,
        target_route_name: str,
        resource_key: str | None,
        owner_key: str | None,
        max_age_seconds: float,
    ) -> bool:
        """Keep a one-shot, short-lived bridge across the next prompt rewrite."""
        key = self._compaction_system_key(
            source_route_name=source_route_name,
            source_scope_key=source_scope_key,
            payload=payload,
        )
        if not key or not resource_key or not owner_key:
            return False
        now = self._clock()
        with self._lock:
            self._prune(now)
            self._compaction_continuations[key] = _CompactionContinuation(
                target_route_name=target_route_name,
                resource_key=resource_key,
                owner_key=owner_key,
                expires_at=now + max(0.1, float(max_age_seconds)),
            )
            if len(self._compaction_continuations) > self._max_entries:
                oldest = min(
                    self._compaction_continuations, key=lambda item: self._compaction_continuations[item].expires_at
                )
                del self._compaction_continuations[oldest]
        return True

    def consume_compaction_continuation(
        self,
        *,
        source_route_name: str,
        source_scope_key: str,
        payload: Mapping[str, Any] | None,
        allowed_routes: Iterable[str],
    ) -> CompactionHandoff | None:
        key = self._compaction_system_key(
            source_route_name=source_route_name,
            source_scope_key=source_scope_key,
            payload=payload,
        )
        if key is None:
            return None
        with self._lock:
            self._prune(self._clock())
            continuation = self._compaction_continuations.pop(key, None)
        if continuation is None or continuation.target_route_name not in set(allowed_routes):
            return None
        return CompactionHandoff(
            route_name=continuation.target_route_name,
            resource_key=continuation.resource_key,
            owner_key=continuation.owner_key,
            previous_prompt_tokens=0,
            completion_tokens=0,
            context_ratio=0.0,
        )

    def reservation_status(
        self,
        *,
        resource_key: str | None,
        owner_key: str | None,
        routing_priority: int,
    ) -> ReservationLookup:
        """Return access to a resource's soft session reservation.

        ``priority`` reservations protect only against a *strictly lower*
        service class. ``session`` reservations protect the idle resource from
        every other owner until expiry. A higher priority session may still
        preempt a priority reservation, but never a session reservation.
        """
        if not resource_key:
            return ReservationLookup("not_applicable")
        now = self._clock()
        with self._lock:
            self._prune(now)
            reservation = self._reservations.get(resource_key)
            if reservation is None:
                return ReservationLookup("none")
            remaining = max(0.0, reservation.expires_at - now)
            if owner_key and hmac.compare_digest(owner_key, reservation.owner_key):
                return ReservationLookup(
                    "owner",
                    remaining,
                    reservation.mode,
                    reservation.routing_priority,
                    reservation.context_ratio,
                )
            if reservation.mode == "session":
                return ReservationLookup(
                    "reserved_by_other_session",
                    remaining,
                    reservation.mode,
                    reservation.routing_priority,
                    reservation.context_ratio,
                )
            if reservation.routing_priority > int(routing_priority):
                return ReservationLookup(
                    "reserved_by_higher_priority",
                    remaining,
                    reservation.mode,
                    reservation.routing_priority,
                    reservation.context_ratio,
                )
            if reservation.routing_priority < int(routing_priority):
                return ReservationLookup(
                    "preemptable",
                    remaining,
                    reservation.mode,
                    reservation.routing_priority,
                    reservation.context_ratio,
                )
            return ReservationLookup(
                "equal_priority",
                remaining,
                reservation.mode,
                reservation.routing_priority,
                reservation.context_ratio,
            )

    def reserve(
        self,
        *,
        resource_key: str | None,
        owner_key: str | None,
        route_name: str,
        routing_priority: int,
        mode: str = "priority",
        timeout_seconds: float | None = None,
        prompt_tokens: Any,
        cached_prompt_tokens: Any = None,
        min_prompt_tokens: int = 0,
        min_cached_prompt_tokens: int = 0,
        ctx_len: Any,
        min_seconds: float = DEFAULT_RESERVATION_MIN_SECONDS,
        max_seconds: float = DEFAULT_RESERVATION_MAX_SECONDS,
        min_context_ratio: float = DEFAULT_RESERVATION_MIN_CONTEXT_RATIO,
        max_context_ratio: float = DEFAULT_RESERVATION_MAX_CONTEXT_RATIO,
    ) -> ReservationLookup:
        """Create/update a soft resource lease after a successful turn.

        A fixed configured timeout takes precedence; otherwise duration scales
        linearly between the configured context occupancy bounds. No message
        data is stored: the registry retains only opaque owner keys and
        aggregate token/context facts.
        """
        if not resource_key or not owner_key:
            return ReservationLookup("not_applicable")
        if mode not in {"priority", "session"}:
            return ReservationLookup("invalid_mode")
        try:
            prompt = max(0, int(prompt_tokens))
            context = max(1, int(ctx_len))
        except (TypeError, ValueError):
            return ReservationLookup("usage_unavailable")
        try:
            minimum_prompt = max(0, int(min_prompt_tokens))
            minimum_cached_prompt = max(0, int(min_cached_prompt_tokens))
        except (TypeError, ValueError):
            return ReservationLookup("invalid_minimum")
        if prompt < minimum_prompt:
            return ReservationLookup("below_minimum_prompt")
        if minimum_cached_prompt:
            try:
                cached = max(0, int(cached_prompt_tokens))
            except (TypeError, ValueError):
                return ReservationLookup("cached_usage_unavailable")
            if cached < minimum_cached_prompt:
                return ReservationLookup("below_minimum_cached_prompt")
        minimum = max(1.0, float(min_seconds))
        maximum = max(minimum, float(max_seconds))
        low = max(0.0, min(1.0, float(min_context_ratio)))
        high = max(low, min(1.0, float(max_context_ratio)))
        ratio = min(1.0, prompt / context)
        if timeout_seconds is not None:
            try:
                duration = max(1.0, float(timeout_seconds))
            except (TypeError, ValueError):
                return ReservationLookup("invalid_timeout")
        elif high <= low:
            duration = maximum if ratio >= high else minimum
        else:
            progress = min(1.0, max(0.0, (ratio - low) / (high - low)))
            duration = minimum + (maximum - minimum) * progress
        now = self._clock()
        with self._lock:
            self._prune(now)
            self._reservations[resource_key] = _Reservation(
                owner_key=owner_key,
                route_name=route_name,
                routing_priority=int(routing_priority),
                mode=mode,
                expires_at=now + duration,
                context_ratio=ratio,
            )
        return ReservationLookup("created", duration)

    def release_reservation(
        self,
        *,
        resource_key: str | None,
        owner_key: str | None,
    ) -> bool:
        """Release this session's idle lease after a successful migration."""
        if not resource_key or not owner_key:
            return False
        with self._lock:
            self._prune(self._clock())
            reservation = self._reservations.get(resource_key)
            if reservation is None or not hmac.compare_digest(
                owner_key,
                reservation.owner_key,
            ):
                return False
            del self._reservations[resource_key]
            return True

    def transfer_reservation(
        self,
        *,
        resource_key: str | None,
        previous_owner_key: str | None,
        new_owner_key: str | None,
    ) -> bool:
        """Atomically move an idle lease to a new prompt-derived owner key."""
        if not resource_key or not previous_owner_key or not new_owner_key:
            return False
        with self._lock:
            self._prune(self._clock())
            reservation = self._reservations.get(resource_key)
            if reservation is None or not hmac.compare_digest(
                previous_owner_key,
                reservation.owner_key,
            ):
                return False
            reservation.owner_key = new_owner_key
            return True

    def record_cache_observation(
        self,
        keys: Iterable[str],
        *,
        route_name: str,
        cached_prompt_tokens: Any,
    ) -> None:
        """Attach an upstream-reported cache hit to a live assignment.

        This records that the selected target reused *some* prefix for this
        affinity key. It intentionally does not claim an exact token-prefix
        digest until the target's tokenizer/template equivalence is known.
        """
        try:
            cached = int(cached_prompt_tokens)
        except (TypeError, ValueError):
            return
        if cached <= 0:
            return
        now = self._clock()
        with self._lock:
            self._prune(now)
            for key in keys:
                assignment = self._assignments.get(key)
                if assignment is None or assignment.route_name != route_name:
                    continue
                assignment.cached_prompt_tokens = cached
                assignment.touched_at = now

    def snapshot(self) -> dict[str, int]:
        """Expose only aggregate state for status/diagnostics."""
        with self._lock:
            self._prune(self._clock())
            return {
                "assignments": len(self._assignments),
                "reservations": len(self._reservations),
                "compaction_candidates": sum(map(len, self._compaction_candidates.values())),
            }

    def _prune(self, now: float) -> None:
        expired = [key for key, value in self._assignments.items() if value.expires_at <= now]
        for key in expired:
            del self._assignments[key]
        expired_reservations = [key for key, value in self._reservations.items() if value.expires_at <= now]
        for key in expired_reservations:
            del self._reservations[key]
        for scope_key, candidates in tuple(self._compaction_candidates.items()):
            retained = [candidate for candidate in candidates if candidate.expires_at > now]
            if retained:
                self._compaction_candidates[scope_key] = retained
            else:
                del self._compaction_candidates[scope_key]
        for key, continuation in tuple(self._compaction_continuations.items()):
            if continuation.expires_at <= now:
                del self._compaction_continuations[key]
        overflow = len(self._assignments) - self._max_entries
        if overflow > 0:
            oldest = sorted(self._assignments.items(), key=lambda item: item[1].touched_at)[:overflow]
            for key, _value in oldest:
                del self._assignments[key]


_registry = SessionAffinityRegistry()


def get_session_affinity_registry() -> SessionAffinityRegistry:
    """Return the process-local registry shared by route selectors."""
    return _registry


__all__ = [
    "AffinityLookup",
    "ReservationLookup",
    "CompactionHandoff",
    "SessionAffinityRegistry",
    "get_session_affinity_registry",
]
