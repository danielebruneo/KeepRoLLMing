"""Contracts for bounded process-local routing affinity."""

import pytest

from keeprollming.routing.affinity import (
    DEFAULT_AFFINITY_TTL_SECONDS,
    SessionAffinityRegistry,
)


def test_default_affinity_ttl_is_one_hour():
    assert DEFAULT_AFFINITY_TTL_SECONDS == 60 * 60


def test_affinity_prefers_explicit_client_conversation_id():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    headers = {"x-librechat-conversation-id": "conversation-1"}
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    keys = registry.candidate_keys(
        route_name="public/code",
        headers=headers,
        payload=payload,
    )
    registry.assign(
        registry.assignment_keys(route_name="public/code", headers=headers, payload=payload),
        route_name="code/runner-a",
    )

    lookup = registry.lookup(keys, allowed_routes={"code/runner-a", "code/runner-b"})

    assert lookup.route_name == "code/runner-a"
    assert lookup.state == "hit"


def test_affinity_recognizes_prior_complete_message_prefix_without_client_id():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    first = {"messages": [{"role": "user", "content": "first"}]}
    later = {
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
    }
    registry.assign(
        registry.assignment_keys(route_name="public/code", headers={}, payload=first),
        route_name="code/runner-a",
    )

    lookup = registry.lookup(
        registry.candidate_keys(route_name="public/code", headers={}, payload=later),
        allowed_routes={"code/runner-a"},
    )

    assert lookup.route_name == "code/runner-a"
    assert lookup.state == "hit"


def test_affinity_does_not_reuse_assignment_when_target_is_no_longer_allowed():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    keys = registry.assignment_keys(route_name="public/code", headers={}, payload=payload)
    registry.assign(keys, route_name="code/runner-a")

    lookup = registry.lookup(keys, allowed_routes={"code/runner-b"})

    assert lookup.route_name is None
    assert lookup.state == "ineligible"


def test_affinity_does_not_cross_authenticated_client_scope():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    payload = {"messages": [{"role": "user", "content": "same prompt"}]}
    first_headers = {"authorization": "Bearer first-client"}
    second_headers = {"authorization": "Bearer second-client"}
    registry.assign(
        registry.assignment_keys(
            route_name="public/code",
            headers=first_headers,
            payload=payload,
        ),
        route_name="code/runner-a",
    )

    lookup = registry.lookup(
        registry.candidate_keys(
            route_name="public/code",
            headers=second_headers,
            payload=payload,
        ),
        allowed_routes={"code/runner-a"},
    )

    assert lookup.route_name is None
    assert lookup.state == "miss"


def test_affinity_marks_a_route_as_cache_confirmed_after_upstream_usage():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    keys = registry.assignment_keys(route_name="public/code", headers={}, payload=payload)
    registry.assign(keys, route_name="code/runner-a")
    registry.record_cache_observation(
        keys,
        route_name="code/runner-a",
        cached_prompt_tokens=256,
    )

    lookup = registry.lookup(keys, allowed_routes={"code/runner-a"})

    assert lookup.route_name == "code/runner-a"
    assert lookup.state == "cache_hit"
    assert lookup.cached_prompt_tokens == 256


def test_idle_reservation_blocks_only_lower_priority_sessions():
    now = [10.0]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: now[0])
    owner = "opaque-owner"
    created = registry.reserve(
        resource_key="http://runner :: model",
        owner_key=owner,
        route_name="code/runner-a",
        routing_priority=100,
        prompt_tokens=75_000,
        ctx_len=100_000,
    )

    assert created.state == "created"
    assert created.expires_in_seconds == 15 * 60
    assert (
        registry.reservation_status(
            resource_key="http://runner :: model",
            owner_key="other",
            routing_priority=0,
        ).state
        == "reserved_by_higher_priority"
    )
    assert (
        registry.reservation_status(
            resource_key="http://runner :: model",
            owner_key="other",
            routing_priority=100,
        ).state
        == "equal_priority"
    )
    assert (
        registry.reservation_status(
            resource_key="http://runner :: model",
            owner_key="other",
            routing_priority=101,
        ).state
        == "preemptable"
    )
    assert (
        registry.reservation_status(
            resource_key="http://runner :: model",
            owner_key=owner,
            routing_priority=100,
        ).state
        == "owner"
    )


def test_session_reservation_blocks_every_other_session_until_expiry():
    now = [10.0]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: now[0])
    registry.reserve(
        resource_key="http://runner :: model",
        owner_key="owner-a",
        route_name="code/runner-a",
        routing_priority=1,
        mode="session",
        timeout_seconds=15 * 60,
        prompt_tokens=1,
        ctx_len=100_000,
    )

    assert (
        registry.reservation_status(
            resource_key="http://runner :: model",
            owner_key="owner-b",
            routing_priority=999,
        ).state
        == "reserved_by_other_session"
    )
    assert (
        registry.reservation_status(
            resource_key="http://runner :: model",
            owner_key="owner-a",
            routing_priority=1,
        ).state
        == "owner"
    )

    now[0] += 15 * 60
    assert (
        registry.reservation_status(
            resource_key="http://runner :: model",
            owner_key="owner-b",
            routing_priority=999,
        ).state
        == "none"
    )


def test_idle_reservation_skips_short_or_uncached_turns_when_configured():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    common = {
        "resource_key": "runner",
        "owner_key": "owner",
        "route_name": "runner",
        "routing_priority": 1,
        "ctx_len": 100_000,
        "min_prompt_tokens": 4_096,
        "min_cached_prompt_tokens": 2_048,
    }

    short = registry.reserve(
        prompt_tokens=4_095,
        cached_prompt_tokens=4_095,
        **common,
    )
    unknown_cache = registry.reserve(
        prompt_tokens=4_096,
        cached_prompt_tokens=None,
        **common,
    )
    cold_cache = registry.reserve(
        prompt_tokens=4_096,
        cached_prompt_tokens=2_047,
        **common,
    )
    warm = registry.reserve(
        prompt_tokens=4_096,
        cached_prompt_tokens=2_048,
        **common,
    )

    assert short.state == "below_minimum_prompt"
    assert unknown_cache.state == "cached_usage_unavailable"
    assert cold_cache.state == "below_minimum_cached_prompt"
    assert warm.state == "created"


def test_idle_reservation_duration_scales_with_context_occupancy():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    low = registry.reserve(
        resource_key="low",
        owner_key="owner",
        route_name="runner",
        routing_priority=1,
        prompt_tokens=10_000,
        ctx_len=100_000,
    )
    middle = registry.reserve(
        resource_key="middle",
        owner_key="owner",
        route_name="runner",
        routing_priority=1,
        prompt_tokens=42_500,
        ctx_len=100_000,
    )
    high = registry.reserve(
        resource_key="high",
        owner_key="owner",
        route_name="runner",
        routing_priority=1,
        prompt_tokens=75_000,
        ctx_len=100_000,
    )

    assert low.expires_in_seconds == 60
    assert middle.expires_in_seconds == pytest.approx(480)
    assert high.expires_in_seconds == 900


def test_compaction_candidate_hands_off_once_to_new_prefix_and_transfers_reservation():
    now = [100.0]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: now[0])
    old_owner = "old-prefix-owner"
    new_owner = "new-prefix-owner"
    assert (
        registry.reserve(
            resource_key="runner-a :: qwen",
            owner_key=old_owner,
            route_name="code/runner-a",
            routing_priority=10,
            mode="session",
            timeout_seconds=900,
            prompt_tokens=226_000,
            ctx_len=262_144,
        ).state
        == "created"
    )
    assert (
        registry.record_compaction_candidate(
            source_route_name="code/architect",
            source_scope_key="client-a",
            target_route_name="code/runner-a",
            resource_key="runner-a :: qwen",
            owner_key=old_owner,
            prompt_tokens=226_000,
            completion_tokens=15_293,
            ctx_len=262_144,
            max_age_seconds=60,
            min_context_ratio=0.80,
            min_completion_tokens=5_000,
        )
        == "candidate_recorded"
    )

    handoff = registry.consume_compaction_candidate(
        source_route_name="code/architect",
        source_scope_key="client-a",
        new_prompt_tokens=34_260,
        allowed_routes={"code/runner-a", "code/runner-b"},
        min_prompt_reduction_ratio=0.50,
    )
    assert handoff is not None
    assert handoff.route_name == "code/runner-a"
    assert handoff.owner_key == old_owner
    assert (
        registry.consume_compaction_candidate(
            source_route_name="code/architect",
            source_scope_key="client-a",
            new_prompt_tokens=34_260,
            allowed_routes={"code/runner-a"},
            min_prompt_reduction_ratio=0.50,
        )
        is None
    )

    registry.transfer_reservation(
        resource_key="runner-a :: qwen",
        previous_owner_key=old_owner,
        new_owner_key=new_owner,
    )
    assert (
        registry.reservation_status(
            resource_key="runner-a :: qwen",
            owner_key=new_owner,
            routing_priority=10,
        ).state
        == "owner"
    )
    assert (
        registry.reservation_status(
            resource_key="runner-a :: qwen",
            owner_key=old_owner,
            routing_priority=10,
        ).state
        == "reserved_by_other_session"
    )


@pytest.mark.parametrize(
    ("prompt_tokens", "completion_tokens", "allowed_routes", "expected"),
    [
        (100_000, 15_000, {"runner-a"}, False),
        (226_000, 4_999, {"runner-a"}, False),
        (226_000, 15_000, {"runner-b"}, False),
    ],
)
def test_compaction_candidates_reject_weak_signals(prompt_tokens, completion_tokens, allowed_routes, expected):
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    registry.record_compaction_candidate(
        source_route_name="code/architect",
        source_scope_key="client-a",
        target_route_name="runner-a",
        resource_key="runner-a :: qwen",
        owner_key="owner",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        ctx_len=262_144,
        max_age_seconds=60,
        min_context_ratio=0.80,
        min_completion_tokens=5_000,
    )
    claimed = registry.consume_compaction_candidate(
        source_route_name="code/architect",
        source_scope_key="client-a",
        new_prompt_tokens=34_260,
        allowed_routes=allowed_routes,
        min_prompt_reduction_ratio=0.50,
    )
    assert (claimed is not None) is expected


def test_compaction_candidate_expires_after_configured_window():
    now = [10.0]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: now[0])
    registry.record_compaction_candidate(
        source_route_name="code/architect",
        source_scope_key="client-a",
        target_route_name="runner-a",
        resource_key="runner-a :: qwen",
        owner_key="owner",
        prompt_tokens=226_000,
        completion_tokens=15_000,
        ctx_len=262_144,
        max_age_seconds=60,
        min_context_ratio=0.80,
        min_completion_tokens=5_000,
    )
    now[0] += 60.1
    assert (
        registry.consume_compaction_candidate(
            source_route_name="code/architect",
            source_scope_key="client-a",
            new_prompt_tokens=34_260,
            allowed_routes={"runner-a"},
            min_prompt_reduction_ratio=0.50,
        )
        is None
    )


def test_compaction_candidates_are_isolated_by_opaque_client_scope():
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    registry.record_compaction_candidate(
        source_route_name="code/architect",
        source_scope_key="credential-and-origin-a",
        target_route_name="runner-a",
        resource_key="runner-a :: qwen",
        owner_key="owner",
        prompt_tokens=226_000,
        completion_tokens=15_000,
        ctx_len=262_144,
        max_age_seconds=60,
        min_context_ratio=0.80,
        min_completion_tokens=5_000,
    )

    assert (
        registry.consume_compaction_candidate(
            source_route_name="code/architect",
            source_scope_key="credential-and-origin-b",
            new_prompt_tokens=34_000,
            allowed_routes={"runner-a"},
            min_prompt_reduction_ratio=0.50,
        )
        is None
    )


def test_compaction_continuation_survives_middle_prompt_rewrite_only_once():
    now = [10.0]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: now[0])
    original = {
        "messages": [
            {"role": "system", "content": "project instructions"},
            {"role": "user", "content": "restored snapshot"},
        ]
    }
    rewritten = {
        "messages": [
            {"role": "system", "content": "project instructions"},
            {"role": "user", "content": "different skills reminder"},
        ]
    }
    assert registry.record_compaction_continuation(
        source_route_name="code/senior",
        source_scope_key="client-a",
        payload=original,
        target_route_name="pool/swift-3090ti",
        resource_key="7804 :: swift",
        owner_key="post-compaction-owner",
        max_age_seconds=60,
    )

    handoff = registry.consume_compaction_continuation(
        source_route_name="code/senior",
        source_scope_key="client-a",
        payload=rewritten,
        allowed_routes={"pool/swift-3090ti"},
    )
    assert handoff is not None
    assert handoff.route_name == "pool/swift-3090ti"
    assert handoff.owner_key == "post-compaction-owner"
    assert (
        registry.consume_compaction_continuation(
            source_route_name="code/senior",
            source_scope_key="client-a",
            payload=rewritten,
            allowed_routes={"pool/swift-3090ti"},
        )
        is None
    )


@pytest.mark.parametrize(
    ("route_name", "scope_key", "system_text", "allowed_routes"),
    [
        ("code/junior", "client-a", "project instructions", {"pool/swift-3090ti"}),
        ("code/senior", "client-b", "project instructions", {"pool/swift-3090ti"}),
        ("code/senior", "client-a", "other project instructions", {"pool/swift-3090ti"}),
        ("code/senior", "client-a", "project instructions", {"pool/swift-v100"}),
    ],
)
def test_compaction_continuation_rejects_wrong_scope_or_target(route_name, scope_key, system_text, allowed_routes):
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    assert registry.record_compaction_continuation(
        source_route_name="code/senior",
        source_scope_key="client-a",
        payload={"messages": [{"role": "system", "content": "project instructions"}]},
        target_route_name="pool/swift-3090ti",
        resource_key="7804 :: swift",
        owner_key="owner",
        max_age_seconds=60,
    )
    assert (
        registry.consume_compaction_continuation(
            source_route_name=route_name,
            source_scope_key=scope_key,
            payload={"messages": [{"role": "system", "content": system_text}]},
            allowed_routes=allowed_routes,
        )
        is None
    )


def test_compaction_continuation_expires_with_handoff_window():
    now = [10.0]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: now[0])
    payload = {"messages": [{"role": "system", "content": "project instructions"}]}
    assert registry.record_compaction_continuation(
        source_route_name="code/senior",
        source_scope_key="client-a",
        payload=payload,
        target_route_name="pool/swift-3090ti",
        resource_key="7804 :: swift",
        owner_key="owner",
        max_age_seconds=60,
    )
    now[0] += 60.1
    assert (
        registry.consume_compaction_continuation(
            source_route_name="code/senior",
            source_scope_key="client-a",
            payload=payload,
            allowed_routes={"pool/swift-3090ti"},
        )
        is None
    )
