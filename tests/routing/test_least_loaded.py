"""Least-loaded selection uses backend admission counts without breaking pins."""

import asyncio

import pytest
from fastapi.responses import JSONResponse

from keeprollming.routing import BalanceConfigurationError, Route, resolve_balance_selection
from keeprollming.routing.balancing import parse_balance_targets


@pytest.mark.parametrize("invalid", [None, True, 1, [], {}, "round_robin", ""])
def test_selection_policy_validation(invalid):
    with pytest.raises(BalanceConfigurationError, match="selection_policy"):
        parse_balance_targets({"targets": ["a"], "selection_policy": invalid}, route_name="selector")


def test_selection_policy_inherits_and_validates_at_config_boundary():
    from keeprollming.config import load_user_routes, validate_resolved_route_balance
    from keeprollming.routing.router import resolve_inherited_route

    routes = load_user_routes(
        {
            "routes": {
                "template": {"is_private": True, "balance": {"selection_policy": "least_loaded", "targets": ["a"]}},
                "select": {"extends": "template"},
                "a": {"model": "m", "upstream_url": "http://a"},
            }
        }
    )
    validate_resolved_route_balance(routes)
    by_name = {r.name: r for r in routes}
    assert resolve_inherited_route(by_name["select"], by_name).balance["selection_policy"] == "least_loaded"
    by_name["template"].balance["selection_policy"] = "typo"
    with pytest.raises(ValueError, match="selection_policy"):
        validate_resolved_route_balance(routes)


def test_static_and_nested_selection_preserve_stable_ties():
    a = Route(name="a", pattern="a", model="m", upstream_url="http://a")
    b = Route(name="b", pattern="b", model="m", upstream_url="http://b")
    inner = Route(name="inner", pattern="inner", balance={"targets": ["a", "b"]})
    outer = Route(
        name="outer",
        pattern="outer",
        balance={
            "selection_policy": "least_loaded",
            "targets": ["inner"],
        },
    )
    by_name = {r.name: r for r in (outer, inner, a, b)}
    assert resolve_balance_selection(outer, routes_by_name=by_name).selected_route.name == "a"
    assert (
        resolve_balance_selection(
            outer, routes_by_name=by_name, active_requests=lambda r: int(r.name == "a")
        ).selected_route.name
        == "b"
    )
    assert (
        resolve_balance_selection(outer, routes_by_name=by_name, active_requests=lambda _: 1).selected_route.name == "a"
    )


@pytest.mark.parametrize(
    ("policy", "loads", "preferred", "eligible", "expected"),
    [
        ("priority", (1, 0), None, ("a", "b"), "a"),
        ("least_loaded", (1, 0), None, ("a", "b"), "b"),
        ("least_loaded", (2, 1), None, ("a", "b"), "b"),
        ("least_loaded", (1, 1), None, ("a", "b"), "a"),
        ("least_loaded", (1, 0), "a", ("a", "b"), "a"),
        ("least_loaded", (0, 1), None, ("b",), "b"),
        ("least_loaded", (1, 0), "a", ("b",), "b"),
    ],
)
def test_selection_order(policy, loads, preferred, eligible, expected):
    a = Route(name="a", pattern="a", model="m", upstream_url="http://a")
    b = Route(name="b", pattern="b", model="m", upstream_url="http://b")
    selector = Route(
        name="selector",
        pattern="selector",
        balance={
            "selection_policy": policy,
            "targets": [{"route": "b", "priority": 10}, {"route": "a", "priority": 100}],
        },
    )
    counts = dict(zip(("a", "b"), loads))
    selection = resolve_balance_selection(
        selector,
        routes_by_name={r.name: r for r in (selector, a, b)},
        active_requests=lambda r: counts[r.name],
        preferred_route_name=preferred,
        is_eligible=lambda r: r.name in eligible,
    )
    assert selection.selected_route.name == expected


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize(
    "policy,counts,expected",
    [
        ("priority", (1, 0), "a"),
        ("least_loaded", (1, 0), "b"),
        ("least_loaded", (2, 1), "b"),
        ("least_loaded", (1, 1), "a"),
        ("least_loaded", (3, 0), "b"),
    ],
)
def test_endpoint_uses_shared_resource_counts(monkeypatch, stream, pinned, policy, counts, expected):
    import keeprollming.routing as routing
    from keeprollming import admission, config
    from keeprollming.endpoints import chat_completions as chat
    from keeprollming.routing.resources import backend_resource_key

    selector = Route(
        name="select",
        pattern="select",
        api_keys=[],
        balance={
            "selection_policy": policy,
            "targets": [{"route": "a", "priority": 100}, {"route": "b", "priority": 50}],
        },
    )
    a = Route(name="a", pattern="a", model="m", upstream_url="http://a", max_concurrent=3)
    b = Route(name="b", pattern="b", model="m", upstream_url="http://b", max_concurrent=3)
    routes = [selector, a, b]
    controller = admission.RouteAdmissionController()
    registry = routing.SessionAffinityRegistry(secret=b"l" * 32)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    seen = {}
    decision = {}
    payload = {"model": "select", "stream": stream, "messages": [{"role": "user", "content": "load-test"}]}
    headers = {"x-librechat-conversation-id": "load-test-session"}
    if pinned:
        selector.balance["promote_when_beneficial"] = False
        owner = registry.assign(
            registry.assignment_keys(route_name=selector.name, headers=headers, payload=payload),
            route_name="a",
        )
        registry.reserve(
            resource_key=str(backend_resource_key(a)),
            owner_key=owner,
            route_name="a",
            routing_priority=0,
            mode="session",
            timeout_seconds=900,
            prompt_tokens=500,
            ctx_len=10000,
        )
        if counts[0] < 3:
            expected = "a"

    async def nonstream(**kwargs):
        seen.update(kwargs)
        return JSONResponse({"usage": {"prompt_tokens": 10}})

    async def streaming(**kwargs):
        seen.update(kwargs)
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat, "USER_ROUTES", routes)
    monkeypatch.setattr(chat, "resolve_route", lambda *_args, **_kw: (selector, "unused"))
    monkeypatch.setattr(admission, "get_route_admission_controller", lambda: controller)
    monkeypatch.setattr(chat, "process_non_streaming_request", nonstream)
    monkeypatch.setattr(chat, "process_streaming_request", streaming)
    monkeypatch.setattr(chat._exec, "emit_balance_decision", lambda _id, **kw: decision.update(kw))

    async def run():
        leases = []
        try:
            for route, count in zip((a, b), counts):
                for i in range(count):
                    # Different route names, same physical endpoint/model:
                    # activity from other client-facing routes must count too.
                    leases.append(
                        await controller.acquire(
                            req_id=f"held-{route.name}-{i}",
                            route_name=f"other/{route.name}",
                            max_concurrent=3,
                            queue_timeout=0,
                            resource_key=str(backend_resource_key(route)),
                        )
                    )
            result = await chat.process_chat_request(payload, headers, "least-load-test")
            if stream:
                assert b"".join([chunk async for chunk in result]) == b"data: [DONE]\n\n"
            else:
                assert result.status_code == 200
        finally:
            for lease in leases:
                await lease.release()

    asyncio.run(run())
    assert seen["route"].name == expected
    assert decision["selection_policy"] == policy
    if policy == "least_loaded":
        assert decision["candidate_loads"]["a"]["active"] == counts[0]
        assert decision["candidate_loads"]["b"]["active"] == counts[1]
        assert decision["candidate_loads"]["a"]["capacity"] == 3
