"""Endpoint-level contracts for one balanced route execution decision."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.responses import JSONResponse

from keeprollming.types import Route


def test_non_streaming_request_executes_selected_balance_target(monkeypatch):
    """The selector authorizes the request, but target settings drive execution."""
    from keeprollming import config
    from keeprollming.endpoints import chat_completions

    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-unused",
        api_keys=[],
        fallback_chain=["code/fallback"],
        overrides={"reasoning_effort": "xhigh", "temperature": 0.9},
        balance={
            "targets": ["code/runner-a", "code/runner-b"],
            "preserve_overrides": ["reasoning_effort"],
            "preserve_filters": ["tool_rewrite"],
        },
        filters={"tool_rewrite": {"enabled": True, "supported_patterns": ["nested"]}},
    )
    runner_a = Route(
        name="code/runner-a",
        pattern="code/runner-a",
        model="model-a",
        upstream_url="http://runner-a:8000",
        upstream_headers={"X-Runner": "a"},
        capabilities=["reasoning"],
        filters={"tool_rewrite": {"enabled": False}},
        overrides={"reasoning_effort": "low", "temperature": 0.2},
    )
    runner_b = Route(
        name="code/runner-b",
        pattern="code/runner-b",
        model="model-b",
        upstream_url="http://runner-b:8000",
        capabilities=["reasoning"],
    )
    fallback = Route(
        name="code/fallback",
        pattern="code/fallback",
        model="fallback-model",
        upstream_url="http://fallback:8000",
        capabilities=["reasoning"],
    )
    routes = [selector, runner_a, runner_b, fallback]
    seen = {}

    async def fake_non_streaming(**kwargs):
        seen.update(kwargs)
        return JSONResponse({"ok": True})

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(
        chat_completions,
        "resolve_route",
        lambda _model, req_id=None: (selector, selector.model),
    )
    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)

    response = asyncio.run(
        chat_completions.process_chat_request(
            {
                "model": "public/code",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            {},
            "balance-endpoint",
        )
    )

    assert response.status_code == 200
    assert seen["url"] == "http://runner-a:8000/v1/chat/completions"
    assert seen["upstream_model"] == "model-a"
    assert seen["route"].name == "code/runner-a"
    assert seen["route_headers"] == {"X-Runner": "a"}
    assert seen["payload"]["reasoning_effort"] == "xhigh"
    assert seen["payload"]["temperature"] == 0.2
    assert "tool_rewrite" in seen["enabled_filters"]
    assert [(attempt.route_name, attempt.model) for attempt in seen["fallback_attempts"]] == [
        ("code/runner-a", "model-a"),
        ("code/runner-b", "model-b"),
        ("code/fallback", "fallback-model"),
    ]


def test_streaming_request_executes_selected_balance_target(monkeypatch):
    """Streaming takes the same selected route and ordered retry plan."""
    from keeprollming import config
    from keeprollming.endpoints import chat_completions

    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-unused",
        api_keys=[],
        balance={"targets": ["code/runner-a", "code/runner-b"]},
    )
    runner_a = Route(
        name="code/runner-a",
        pattern="code/runner-a",
        model="model-a",
        upstream_url="http://runner-a:8000",
        upstream_headers={"X-Runner": "a"},
    )
    runner_b = Route(
        name="code/runner-b",
        pattern="code/runner-b",
        model="model-b",
        upstream_url="http://runner-b:8000",
    )
    routes = [selector, runner_a, runner_b]
    seen = {}

    async def fake_streaming(**kwargs):
        seen.update(kwargs)
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(
        chat_completions,
        "resolve_route",
        lambda _model, req_id=None: (selector, selector.model),
    )
    monkeypatch.setattr(chat_completions, "process_streaming_request", fake_streaming)

    async def run():
        stream = await chat_completions.process_chat_request(
            {
                "model": "public/code",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
            {},
            "balance-streaming",
        )
        return b"".join([chunk async for chunk in stream])

    assert asyncio.run(run()) == b"data: [DONE]\n\n"
    assert seen["url"] == "http://runner-a:8000/v1/chat/completions"
    assert seen["upstream_model"] == "model-a"
    assert seen["route"].name == "code/runner-a"
    assert seen["route_headers"] == {"X-Runner": "a"}
    assert [(attempt.route_name, attempt.model) for attempt in seen["fallback_attempts"]] == [
        ("code/runner-a", "model-a"),
        ("code/runner-b", "model-b"),
    ]


def test_balance_capability_enforcement_is_opt_in_at_the_endpoint(monkeypatch):
    """Incomplete capability metadata must not block a selector by default."""
    from keeprollming import config
    from keeprollming.endpoints import chat_completions

    text = Route(
        name="pool/text",
        pattern="pool/text",
        model="text-model",
        upstream_url="http://text:8000",
        capabilities=["chat"],
    )
    tools = Route(
        name="pool/tools",
        pattern="pool/tools",
        model="tool-model",
        upstream_url="http://tools:8000",
        capabilities=["chat", "tools"],
    )
    seen: list[str] = []

    async def fake_non_streaming(**kwargs):
        seen.append(kwargs["route"].name)
        return JSONResponse({"usage": {"prompt_tokens": 10}})

    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)

    for enforce, expected in ((False, "pool/text"), (True, "pool/tools")):
        selector = Route(
            name="public/agent",
            pattern="public/agent",
            model="selector-unused",
            api_keys=[],
            balance={
                "targets": ["pool/text", "pool/tools"],
                "enforce_capabilities": enforce,
            },
        )
        routes = [selector, text, tools]
        monkeypatch.setattr(config, "USER_ROUTES", routes)
        monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
        monkeypatch.setattr(
            chat_completions,
            "resolve_route",
            lambda _model, req_id=None: (selector, selector.model),
        )

        response = asyncio.run(
            chat_completions.process_chat_request(
                {
                    "model": "public/agent",
                    "messages": [{"role": "user", "content": "use a tool"}],
                    "tools": [{"type": "function", "function": {"name": "read_file"}}],
                    "stream": False,
                },
                {},
                f"capability-enforcement-{enforce}",
            )
        )

        assert response.status_code == 200
        assert seen[-1] == expected


def test_balance_skips_resource_reserved_by_higher_priority_session(monkeypatch):
    """A soft code-session lease sends a lower-priority route to its next pool target."""
    import keeprollming.routing as routing
    from keeprollming import config
    from keeprollming.endpoints import chat_completions
    from keeprollming.routing.affinity import SessionAffinityRegistry
    from keeprollming.routing.resources import backend_resource_key

    selector = Route(
        name="bot/main",
        pattern="bot/main",
        model="selector-unused",
        api_keys=[],
        routing_priority=-100,
        balance={"targets": ["pool/fast", "pool/slow"]},
    )
    fast = Route(
        name="pool/fast",
        pattern="pool/fast",
        model="qwen",
        upstream_url="http://fast:8000",
        ctx_len=100_000,
    )
    slow = Route(
        name="pool/slow",
        pattern="pool/slow",
        model="qwen",
        upstream_url="http://slow:8000",
        ctx_len=100_000,
    )
    routes = [selector, fast, slow]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    registry.reserve(
        resource_key=str(backend_resource_key(fast)),
        owner_key="code-owner",
        route_name=fast.name,
        routing_priority=100,
        prompt_tokens=75_000,
        ctx_len=100_000,
    )
    seen = {}
    decision = {}

    async def fake_non_streaming(**kwargs):
        seen.update(kwargs)
        return JSONResponse({"usage": {"prompt_tokens": 10}})

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    monkeypatch.setattr(
        chat_completions,
        "resolve_route",
        lambda _model, req_id=None: (selector, selector.model),
    )
    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)
    monkeypatch.setattr(
        chat_completions._exec,
        "emit_balance_decision",
        lambda _req_id, **data: decision.update(data),
    )

    response = asyncio.run(
        chat_completions.process_chat_request(
            {
                "model": "bot/main",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            {},
            "reserved-balance-endpoint",
        )
    )

    assert response.status_code == 200
    assert seen["url"] == "http://slow:8000/v1/chat/completions"
    assert decision["candidate_states"] == {
        "pool/fast": "reserved_by_higher_priority",
        "pool/slow": "eligible",
    }


def test_balance_bypasses_an_idle_reservation_when_no_other_target_exists(monkeypatch):
    """A soft idle lease must not become a terminal selector error."""
    import keeprollming.routing as routing
    from keeprollming import config
    from keeprollming.endpoints import chat_completions
    from keeprollming.routing.affinity import SessionAffinityRegistry
    from keeprollming.routing.resources import backend_resource_key

    selector = Route(
        name="bot/main",
        pattern="bot/main",
        model="selector-unused",
        api_keys=[],
        routing_priority=-100,
        queue_timeout=0,
        balance={"targets": ["pool/fast"]},
    )
    fast = Route(
        name="pool/fast",
        pattern="pool/fast",
        model="qwen",
        upstream_url="http://fast:8000",
        ctx_len=100_000,
    )
    routes = [selector, fast]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    registry.reserve(
        resource_key=str(backend_resource_key(fast)),
        owner_key="code-owner",
        route_name=fast.name,
        routing_priority=100,
        prompt_tokens=75_000,
        ctx_len=100_000,
    )
    seen = {}
    decision = {}

    async def fake_non_streaming(**kwargs):
        seen.update(kwargs)
        return JSONResponse({"usage": {"prompt_tokens": 10}})

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    monkeypatch.setattr(
        chat_completions,
        "resolve_route",
        lambda _model, req_id=None: (selector, selector.model),
    )
    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)
    monkeypatch.setattr(
        chat_completions._exec,
        "emit_balance_decision",
        lambda _req_id, **data: decision.update(data),
    )

    response = asyncio.run(
        chat_completions.process_chat_request(
            {
                "model": "bot/main",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            {},
            "reservation-bypass-endpoint",
        )
    )

    assert response.status_code == 200
    assert seen["route"].name == "pool/fast"
    assert decision["selection_reason"] == "reservation_bypass_after_wait"
    assert decision["candidate_states"] == {"pool/fast": "reservation_bypassed"}


def test_balance_retries_a_busy_pool_within_the_route_queue_budget(monkeypatch):
    """A short capacity race should not become an immediate selector 503."""
    from keeprollming import admission, config
    from keeprollming.endpoints import chat_completions

    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-unused",
        api_keys=[],
        queue_timeout=0.05,
        balance={"targets": ["pool/fast"], "selection_retry_interval_ms": 1},
    )
    fast = Route(
        name="pool/fast",
        pattern="pool/fast",
        model="qwen",
        upstream_url="http://fast:8000",
        max_concurrent=None,
    )
    routes = [selector, fast]
    decision = {}
    operational = []

    class TransientlyBusyAdmission:
        checks = 0

        def has_available_capacity(self, **_kwargs):
            self.checks += 1
            return self.checks >= 4

        async def acquire(self, **_kwargs):
            class Lease:
                async def release(self):
                    return None

            return Lease()

    admission_controller = TransientlyBusyAdmission()
    monkeypatch.setattr(
        chat_completions._exec,
        "emit_execution_event",
        lambda _id, event_type, **data: operational.append((event_type, data)),
    )

    async def fake_non_streaming(**kwargs):
        return JSONResponse({"usage": {"prompt_tokens": 10}})

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(admission, "get_route_admission_controller", lambda: admission_controller)
    monkeypatch.setattr(
        chat_completions,
        "resolve_route",
        lambda _model, req_id=None: (selector, selector.model),
    )
    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)
    monkeypatch.setattr(
        chat_completions._exec,
        "emit_balance_decision",
        lambda _req_id, **data: decision.update(data),
    )

    response = asyncio.run(
        chat_completions.process_chat_request(
            {
                "model": "public/code",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            {},
            "capacity-retry-endpoint",
        )
    )

    assert response.status_code == 200
    assert admission_controller.checks >= 2
    assert decision["selection_reason"] == "waited_for_capacity"
    assert decision["selection_waited"] is True
    assert decision["selection_retry_count"] >= 1
    waiting = [data for event_type, data in operational if event_type == "execution.routing.balance_waiting"]
    assert len(waiting) == 1  # Poll retries do not spam BASIC.
    assert waiting[0]["candidate_states"] == {"pool/fast": "capacity_unavailable"}
    assert decision["selection_wait_ms"] >= 0


def test_balance_skips_resource_reserved_by_another_session(monkeypatch):
    """Session reservations remain exclusive even to a higher service class."""
    import keeprollming.routing as routing
    from keeprollming import config
    from keeprollming.endpoints import chat_completions
    from keeprollming.routing.affinity import SessionAffinityRegistry
    from keeprollming.routing.resources import backend_resource_key

    selector = Route(
        name="code/architect",
        pattern="code/architect",
        model="selector-unused",
        api_keys=[],
        routing_priority=999,
        balance={"targets": ["pool/fast", "pool/slow"]},
    )
    fast = Route(
        name="pool/fast",
        pattern="pool/fast",
        model="qwen",
        upstream_url="http://fast:8000",
        ctx_len=100_000,
    )
    slow = Route(
        name="pool/slow",
        pattern="pool/slow",
        model="qwen",
        upstream_url="http://slow:8000",
        ctx_len=100_000,
    )
    routes = [selector, fast, slow]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    registry.reserve(
        resource_key=str(backend_resource_key(fast)),
        owner_key="junior-owner",
        route_name=fast.name,
        routing_priority=100,
        mode="session",
        timeout_seconds=15 * 60,
        prompt_tokens=1,
        ctx_len=100_000,
    )
    seen = {}
    decision = {}

    async def fake_non_streaming(**kwargs):
        seen.update(kwargs)
        return JSONResponse({"usage": {"prompt_tokens": 10}})

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    monkeypatch.setattr(
        chat_completions,
        "resolve_route",
        lambda _model, req_id=None: (selector, selector.model),
    )
    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)
    monkeypatch.setattr(
        chat_completions._exec,
        "emit_balance_decision",
        lambda _req_id, **data: decision.update(data),
    )

    response = asyncio.run(
        chat_completions.process_chat_request(
            {
                "model": "code/architect",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
            },
            {},
            "session-reserved-balance-endpoint",
        )
    )

    assert response.status_code == 200
    assert seen["url"] == "http://slow:8000/v1/chat/completions"
    assert decision["candidate_states"] == {
        "pool/fast": "reserved_by_other_session",
        "pool/slow": "eligible",
    }


def test_balance_promotes_expired_session_affinity_to_preferred_target(monkeypatch):
    """After a lease expires, a prior slow-route assignment may scale up."""
    import keeprollming.routing as routing
    from keeprollming import config
    from keeprollming.endpoints import chat_completions
    from keeprollming.routing.affinity import SessionAffinityRegistry

    selector = Route(
        name="public/code",
        pattern="public/code",
        model="selector-unused",
        api_keys=[],
        balance={"targets": ["pool/fast", "pool/slow"]},
    )
    fast = Route(name="pool/fast", pattern="pool/fast", model="qwen", upstream_url="http://fast:8000")
    slow = Route(name="pool/slow", pattern="pool/slow", model="qwen", upstream_url="http://slow:8000")
    routes = [selector, fast, slow]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 10.0)
    payload = {
        "model": "public/code",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }
    headers = {"x-librechat-conversation-id": "promotion-session"}
    old_keys = registry.assignment_keys(route_name=selector.name, headers=headers, payload=payload)
    registry.assign(old_keys, route_name=slow.name)
    seen = {}

    async def fake_non_streaming(**kwargs):
        seen.update(kwargs)
        return JSONResponse({"usage": {"prompt_tokens": 10}})

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    monkeypatch.setattr(chat_completions, "resolve_route", lambda _model, req_id=None: (selector, selector.model))
    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)

    response = asyncio.run(chat_completions.process_chat_request(payload, headers, "promotion-endpoint"))

    assert response.status_code == 200
    assert seen["url"] == "http://fast:8000/v1/chat/completions"


@pytest.mark.parametrize("selection_policy", ["priority", "least_loaded"])
@pytest.mark.parametrize("lookahead_turns", [0, 5])
@pytest.mark.parametrize("shared_backend", [False, True])
def test_balance_promotes_live_session_reservation_only_when_beneficial(
    monkeypatch, selection_policy, lookahead_turns, shared_backend
):
    import keeprollming.app as app
    import keeprollming.routing as routing
    from keeprollming import config
    from keeprollming.endpoints import chat_completions
    from keeprollming.routing.affinity import SessionAffinityRegistry
    from keeprollming.routing.resources import backend_resource_key

    class MeasuredRoutes:
        @staticmethod
        def promotion_estimate(route, policy):
            del policy
            return {
                "pool/slow": {"prompt_tps": 500, "completion_tps": 20},
                "pool/fast": {"prompt_tps": 1000, "completion_tps": 100},
            }.get(route.name)

    async def fake_non_streaming(**kwargs):
        return JSONResponse({"selected": kwargs["route"].name})

    def run_case(promote_when_beneficial):
        selector = Route(
            name="bot/main",
            pattern="bot/main",
            model="selector",
            balance={
                "targets": [
                    {"route": "pool/fast", "priority": 100},
                    {"route": "pool/slow", "priority": 50},
                ],
                "promote_when_beneficial": promote_when_beneficial,
                "selection_policy": selection_policy,
                "promotion": {
                    "lookahead_turns": lookahead_turns,
                    "expected_new_prompt_tokens": 64,
                    "expected_completion_tokens": 128,
                },
            },
        )
        fast = Route(
            name="pool/fast",
            pattern="pool/fast",
            model="qwen",
            upstream_url="http://fast:8000",
        )
        slow = Route(
            name="pool/slow",
            pattern="pool/slow",
            model="qwen",
            upstream_url="http://slow:8000",
        )
        routes = [selector, fast, slow]
        registry = SessionAffinityRegistry(secret=b"p" * 32, clock=lambda: 10.0)
        payload = {
            "model": "bot/main",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 1000,
            "stream": False,
        }
        headers = {"x-librechat-conversation-id": "promotion-session"}
        keys = registry.assignment_keys(
            route_name=selector.name,
            headers=headers,
            payload=payload,
        )
        owner = registry.assign(keys, route_name=slow.name)
        registry.reserve(
            resource_key=str(backend_resource_key(slow)),
            owner_key=owner,
            route_name=slow.name,
            routing_priority=0,
            mode="session",
            timeout_seconds=900,
            prompt_tokens=500,
            cached_prompt_tokens=0,
            ctx_len=10000,
        )
        if shared_backend:
            registry.configure_reservation_policies({str(backend_resource_key(slow)): False})
        monkeypatch.setattr(config, "USER_ROUTES", routes)
        monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
        monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
        monkeypatch.setattr(
            chat_completions,
            "resolve_route",
            lambda _model, req_id=None: (selector, selector.model),
        )
        monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)
        monkeypatch.setattr(app, "get_route_status_registry", lambda: MeasuredRoutes())
        observed = {}
        monkeypatch.setattr(
            chat_completions._exec,
            "emit_balance_decision",
            lambda _req_id, **data: observed.update(data),
        )
        response = asyncio.run(
            chat_completions.process_chat_request(
                payload,
                headers,
                "live-promotion-endpoint",
            )
        )
        return response, registry, owner, slow, observed

    promoted, promoted_registry, promoted_owner, old_slow, decision = run_case(True)
    assert promoted.status_code == 200
    assert b"pool/fast" in promoted.body
    assert decision["selection_reason"] == "promotion_beneficial"
    assert decision["promotion_estimate"]["decision"] == "promote"
    assert decision["promotion_estimate"]["lookahead_turns"] == lookahead_turns
    assert decision["promotion_estimate"]["expected_new_prompt_tokens"] == 64
    assert decision["promotion_estimate"]["expected_completion_tokens"] == 128
    assert promoted_registry.reservation_status(
        resource_key=str(backend_resource_key(old_slow)),
        owner_key=promoted_owner,
        routing_priority=0,
    ).state == ("disabled_by_backend" if shared_backend else "none")

    pinned, _registry, _owner, _slow, disabled_decision = run_case(False)
    assert pinned.status_code == 200
    assert b"pool/slow" in pinned.body
    assert disabled_decision["promotion_estimate"]["decision"] == "disabled"


def test_compacted_agent_prefix_reuses_previous_target_and_transfers_pin(monkeypatch):
    """A large decode near context limit hands the next smaller prompt to its backend."""
    import keeprollming.routing as routing
    from keeprollming import config
    from keeprollming.endpoints import chat_completions
    from keeprollming.routing.affinity import SessionAffinityRegistry

    selector = Route(
        name="code/architect",
        pattern="code/architect",
        model="selector-unused",
        balance={"targets": ["pool/fast", "pool/slow"]},
    )
    fast = Route(
        name="pool/fast",
        pattern="pool/fast",
        model="qwen",
        upstream_url="http://fast:8000",
        ctx_len=262_144,
    )
    slow = Route(
        name="pool/slow",
        pattern="pool/slow",
        model="qwen",
        upstream_url="http://slow:8000",
        ctx_len=262_144,
    )
    routes = [selector, fast, slow]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 100.0)
    decisions = []
    calls = 0

    async def fake_non_streaming(**kwargs):
        nonlocal calls
        calls += 1
        return JSONResponse(
            {
                "usage": {
                    "prompt_tokens": 226_567 if calls == 1 else 34_260,
                    "completion_tokens": 15_293 if calls == 1 else 100,
                }
            }
        )

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    monkeypatch.setattr(
        chat_completions,
        "resolve_route",
        lambda _model, req_id=None: (selector, selector.model),
    )
    monkeypatch.setattr(chat_completions, "process_non_streaming_request", fake_non_streaming)
    monkeypatch.setattr(
        chat_completions._exec,
        "emit_balance_decision",
        lambda _req_id, **data: decisions.append(data),
    )

    first_payload = {
        "model": selector.name,
        "messages": [{"role": "user", "content": "large pre-compaction context"}],
        "stream": False,
    }
    second_payload = {
        "model": selector.name,
        "messages": [
            {"role": "system", "content": "shared project instructions"},
            {"role": "user", "content": "compacted snapshot"},
        ],
        "stream": False,
    }
    third_payload = {
        "model": selector.name,
        "messages": [
            {"role": "system", "content": "shared project instructions"},
            {"role": "user", "content": "different skills reminder"},
            {"role": "assistant", "content": "continuing the work"},
        ],
        "stream": False,
    }
    first = asyncio.run(
        chat_completions.process_chat_request(first_payload, {}, "compaction-before", client_ip="192.0.2.10")
    )
    second = asyncio.run(
        chat_completions.process_chat_request(second_payload, {}, "compaction-after", client_ip="192.0.2.10")
    )
    third = asyncio.run(
        chat_completions.process_chat_request(third_payload, {}, "compaction-next", client_ip="192.0.2.10")
    )

    assert first.status_code == second.status_code == third.status_code == 200
    assert decisions[0]["selected_route"] == "pool/fast"
    assert decisions[1]["selected_route"] == "pool/fast"
    assert decisions[1]["selection_reason"] == "compaction_handoff"
    assert decisions[1]["affinity_state"] == "compaction_handoff"
    assert decisions[2]["selected_route"] == "pool/fast"
    assert decisions[2]["affinity_state"] == "compaction_continuation"
    assert decisions[2]["selection_reason"] == "compaction_continuation"
    assert registry.snapshot()["compaction_candidates"] == 0
    new_keys = registry.assignment_keys(
        route_name=selector.name,
        headers={},
        payload=third_payload,
    )
    new_affinity = registry.lookup(new_keys, allowed_routes={fast.name, slow.name})
    assert new_affinity.route_name == fast.name
    assert (
        registry.reservation_status(
            resource_key="http://fast:8000 :: qwen",
            owner_key=new_affinity.owner_key,
            routing_priority=0,
        ).state
        == "owner"
    )


def test_streaming_compaction_handoff_and_next_prompt_rewrite_keep_backend(monkeypatch):
    """Streaming completion accounting must arm both one-shot affinity bridges."""
    import keeprollming.routing as routing
    from keeprollming import config
    from keeprollming.endpoints import chat_completions
    from keeprollming.routing.affinity import SessionAffinityRegistry

    selector = Route(
        name="code/senior",
        pattern="code/senior",
        model="selector-unused",
        balance={"targets": ["pool/fast", "pool/slow"]},
    )
    fast = Route(
        name="pool/fast",
        pattern="pool/fast",
        model="qwen",
        upstream_url="http://fast:8000",
        ctx_len=262_144,
    )
    slow = Route(
        name="pool/slow",
        pattern="pool/slow",
        model="qwen",
        upstream_url="http://slow:8000",
        ctx_len=262_144,
    )
    routes = [selector, fast, slow]
    registry = SessionAffinityRegistry(secret=b"x" * 32, clock=lambda: 100.0)
    decisions = []
    calls = 0

    async def fake_streaming(**kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_completion"](
            226_567 if calls == 1 else 34_260,
            0,
            15_293 if calls == 1 else 100,
        )
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat_completions, "USER_ROUTES", routes)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    monkeypatch.setattr(chat_completions, "resolve_route", lambda _model, req_id=None: (selector, selector.model))
    monkeypatch.setattr(chat_completions, "process_streaming_request", fake_streaming)
    monkeypatch.setattr(chat_completions._exec, "emit_balance_decision", lambda _req_id, **data: decisions.append(data))

    async def run_request(messages, req_id):
        stream = await chat_completions.process_chat_request(
            {"model": selector.name, "messages": messages, "stream": True},
            {},
            req_id,
            client_ip="192.0.2.10",
        )
        return b"".join([chunk async for chunk in stream])

    async def run_sequence():
        return [
            await run_request([{"role": "user", "content": "long pre-compaction conversation"}], "stream-before"),
            await run_request(
                [
                    {"role": "system", "content": "shared project instructions"},
                    {"role": "user", "content": "restored snapshot"},
                ],
                "stream-after",
            ),
            await run_request(
                [
                    {"role": "system", "content": "shared project instructions"},
                    {"role": "user", "content": "different skills reminder"},
                ],
                "stream-continuation",
            ),
        ]

    assert asyncio.run(run_sequence()) == [b"data: [DONE]\n\n"] * 3
    assert [decision["selected_route"] for decision in decisions] == ["pool/fast"] * 3
    assert [decision["selection_reason"] for decision in decisions[1:]] == [
        "compaction_handoff",
        "compaction_continuation",
    ]
