"""Resource-owned reservation veto; shared lanes retain affinity, not exclusivity."""

import asyncio
from dataclasses import replace

import pytest

from keeprollming.config import load_user_routes, validate_shared_backend_resource_settings
from keeprollming.routing.affinity import SessionAffinityRegistry
from keeprollming.routing.resources import backend_resource_key, resource_reservation_policies
from keeprollming.routing.router import resolve_inherited_route
from keeprollming.types import Route


def pool(name="shared", **kwargs):
    return Route(
        name=name,
        pattern=name,
        model="m",
        upstream_url="http://shared/v1",
        max_concurrent=4,
        allow_idle_reservation=False,
        ctx_len=32000,
        max_tokens=1024,
        **kwargs,
    )


def reserve(registry, key, owner="owner"):
    return registry.reserve(
        resource_key=key,
        owner_key=owner,
        route_name="pool",
        routing_priority=100,
        mode="session",
        timeout_seconds=180,
        prompt_tokens=10000,
        ctx_len=32000,
    )


@pytest.mark.parametrize("list_format", [False, True])
def test_flag_defaults_inherits_and_explicit_true_overrides(list_format):
    raw = {
        "base": {"allow_idle_reservation": False},
        "child": {"extends": "base"},
        "own": {"extends": "base", "allow_idle_reservation": True},
        "default": {},
    }
    if list_format:
        raw = [{"name": name, "pattern": name, **data} for name, data in raw.items()]
    routes = load_user_routes({"routes": raw})
    by = {r.name: r for r in routes}
    assert resolve_inherited_route(by["child"], by).allow_idle_reservation is False
    assert resolve_inherited_route(by["own"], by).allow_idle_reservation is True
    assert resolve_inherited_route(by["default"], by).allow_idle_reservation is True


@pytest.mark.parametrize("bad", [0, 1, "false", [], {}])
def test_invalid_flag_rejected(bad):
    with pytest.raises(ValueError, match="allow_idle_reservation"):
        validate_shared_backend_resource_settings([replace(pool(), allow_idle_reservation=bad)])


def test_aliases_cannot_disagree_on_physical_reservation_policy():
    with pytest.raises(ValueError, match="allow_idle_reservation"):
        validate_shared_backend_resource_settings([pool(), replace(pool("alias"), allow_idle_reservation=True)])
    validate_shared_backend_resource_settings([pool(), pool("alias")])


def test_reload_clears_old_leases_and_stale_callback_cannot_recreate_them():
    registry = SessionAffinityRegistry()
    key = str(backend_resource_key(pool()))
    registry.assign(["session"], route_name="shared", owner_key="owner")
    assert reserve(registry, key).state == "created"

    def stale_callback():
        return reserve(registry, key)

    registry.configure_reservation_policies(resource_reservation_policies([pool()]))
    assert (
        registry.reservation_status(resource_key=key, owner_key="owner", routing_priority=100).state
        == "disabled_by_backend"
    )
    assert stale_callback().state == "disabled_by_backend"
    assert registry.lookup(["session"], allowed_routes={"shared"}).route_name == "shared"
    assert not registry.transfer_reservation(resource_key=key, previous_owner_key="owner", new_owner_key="other")
    # Re-enabling cannot resurrect the deleted lease.
    registry.configure_reservation_policies({key: True})
    assert registry.reservation_status(resource_key=key, owner_key="owner", routing_priority=100).state == "none"
    assert reserve(registry, key).state == "created"


def test_real_reload_applies_veto_and_invalid_reload_preserves_it(monkeypatch):
    import keeprollming.routing.affinity as affinity
    from keeprollming import config

    registry = SessionAffinityRegistry()
    key = str(backend_resource_key(pool()))
    assert reserve(registry, key).state == "created"
    candidate = {
        "defaults": {"ctx_len": 32000, "max_tokens": 1024, "summary_enabled": False},
        "client_access": {"default": "any", "lan_networks": [], "trusted_proxies": []},
        "routes": {"shared": {"model": "m", "upstream_url": "http://shared/v1", "allow_idle_reservation": False}},
    }
    monkeypatch.setattr(config, "CONFIG", {})
    monkeypatch.setattr(config, "USER_ROUTES", [])
    monkeypatch.setattr(config, "DEFAULTS", config.DEFAULTS)
    monkeypatch.setattr(config, "_CONFIG_LAST_MTIME", 0)
    monkeypatch.setattr(config, "get_config_mtime", lambda: 1)
    monkeypatch.setattr(config, "load_config", lambda: candidate)
    monkeypatch.setattr(affinity, "get_session_affinity_registry", lambda: registry)
    assert config.check_config_reload()
    assert reserve(registry, key).state == "disabled_by_backend"
    candidate["routes"]["shared"]["allow_idle_reservation"] = "false"
    monkeypatch.setattr(config, "get_config_mtime", lambda: 2)
    assert not config.check_config_reload()
    assert reserve(registry, key).state == "disabled_by_backend"
    assert config.USER_ROUTES[0].allow_idle_reservation is False


@pytest.mark.parametrize("stream", [False, True])
def test_fallback_completion_belongs_to_shared_resource_not_primary(monkeypatch, stream):
    from fastapi.responses import JSONResponse

    import keeprollming.routing as routing
    from keeprollming import admission, config
    from keeprollming.endpoints import chat_completions as chat

    shared = pool()
    primary = replace(
        shared,
        name="primary",
        pattern="primary",
        upstream_url="http://primary",
        allow_idle_reservation=True,
        fallback_chain=["shared"],
    )
    selector = Route(
        name="code/senior",
        pattern="code/senior",
        balance={"targets": ["primary", "shared"], "idle_reservation": {"mode": "session", "timeout_seconds": 180}},
    )
    routes = [primary, shared, selector]
    by = {r.name: r for r in routes}
    registry = SessionAffinityRegistry()
    registry.configure_reservation_policies(resource_reservation_policies(routes))
    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat, "USER_ROUTES", routes)
    monkeypatch.setattr(chat, "resolve_route", lambda model, req_id=None: (by[model], None))
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)
    controller = admission.RouteAdmissionController()
    monkeypatch.setattr(admission, "get_route_admission_controller", lambda: controller)

    async def nonstream(**kw):
        assert await kw["on_fallback_attempt"](shared)
        return JSONResponse({"usage": {"prompt_tokens": 10000, "completion_tokens": 10}})

    async def streaming(**kw):
        assert await kw["on_fallback_attempt"](shared)
        kw["on_completion"](10000, 0, 10)
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(chat, "process_non_streaming_request", nonstream)
    monkeypatch.setattr(chat, "process_streaming_request", streaming)
    payload = {"model": selector.name, "messages": [{"role": "user", "content": "hello"}], "stream": stream}
    headers = {"x-librechat-conversation-id": "fallback-session"}

    async def run():
        result = await chat.process_chat_request(payload, headers, "fallback-lease")
        if stream:
            [chunk async for chunk in result]
        else:
            assert result.status_code == 200

    asyncio.run(run())
    keys = registry.assignment_keys(route_name=selector.name, headers=headers, payload=payload)
    assert registry.lookup(keys, allowed_routes={"primary", "shared"}).route_name == "shared"
    assert (
        registry.reservation_status(
            resource_key=str(backend_resource_key(primary)), owner_key=None, routing_priority=0
        ).state
        == "none"
    )
    assert (
        registry.reservation_status(
            resource_key=str(backend_resource_key(shared)), owner_key=None, routing_priority=0
        ).state
        == "disabled_by_backend"
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("dedicated_reserved", [False, True])
def test_four_mixed_sessions_share_backend_and_queued_coding_beats_bot(monkeypatch, stream, dedicated_reserved):
    from fastapi.responses import JSONResponse

    import keeprollming.routing as routing
    from keeprollming import admission, config
    from keeprollming.endpoints import chat_completions as chat

    shared = pool()
    selectors = [
        Route(
            name=name,
            pattern=name,
            routing_priority=priority,
            queue_timeout=2,
            balance={"targets": ["shared"], "idle_reservation": {"mode": "session", "timeout_seconds": 180}},
        )
        for name, priority in (("code/senior", 100), ("code/architect", 100), ("bot/main", -100))
    ]
    dedicated = (
        [
            replace(
                shared,
                name=f"dedicated-{i}",
                pattern=f"dedicated-{i}",
                upstream_url=f"http://dedicated-{i}",
                max_concurrent=1,
                allow_idle_reservation=True,
            )
            for i in range(2)
        ]
        if dedicated_reserved
        else []
    )
    if dedicated:
        selectors[0].balance["targets"] = [r.name for r in dedicated] + ["shared"]
    routes = [shared, *dedicated, *selectors]
    by = {r.name: r for r in routes}
    registry = SessionAffinityRegistry()
    registry.configure_reservation_policies(resource_reservation_policies(routes))
    for target in dedicated:
        assert reserve(registry, str(backend_resource_key(target)), owner="other-coding-session").state == "created"
    controller = admission.RouteAdmissionController()
    monkeypatch.setattr(config, "USER_ROUTES", routes)
    monkeypatch.setattr(chat, "USER_ROUTES", routes)
    monkeypatch.setattr(chat, "resolve_route", lambda model, req_id=None: (by[model], None))
    monkeypatch.setattr(admission, "get_route_admission_controller", lambda: controller)
    monkeypatch.setattr(routing, "get_session_affinity_registry", lambda: registry)

    async def run():
        started = []
        gates = {}
        order = []

        async def content(req_id):
            started.append(req_id)
            order.append(req_id)
            await gates[req_id].wait()

        async def nonstream(**kw):
            assert kw["route"].name == "shared"
            await content(kw["req_id"])
            return JSONResponse(
                {"usage": {"prompt_tokens": 10000, "completion_tokens": 10}, "choices": [{"finish_reason": "stop"}]}
            )

        async def streaming(**kw):
            assert kw["route"].name == "shared"
            await content(kw["req_id"])
            kw["on_completion"](10000, 9000, 10)
            yield b"data: [DONE]\n\n"

        monkeypatch.setattr(chat, "process_non_streaming_request", nonstream)
        monkeypatch.setattr(chat, "process_streaming_request", streaming)

        async def request(name, ident):
            response = await chat.process_chat_request(
                {"model": name, "messages": [{"role": "user", "content": ident}], "stream": stream},
                {"x-librechat-conversation-id": ident},
                ident,
            )
            if stream:
                [chunk async for chunk in response]
            else:
                assert response.status_code == 200

        async def wait_until(predicate):
            for _ in range(300):
                if predicate():
                    return
                await asyncio.sleep(0.005)
            raise AssertionError(f"request state did not become ready: started={started}, gates={controller._gates}")

        names = ["code/senior", "code/architect", "bot/main", "bot/main"]
        tasks = []
        try:
            for i, name in enumerate(names):
                ident = f"initial-{i}"
                gates[ident] = asyncio.Event()
                tasks.append(asyncio.create_task(request(name, ident)))
            await wait_until(lambda: len(started) == 4)
            for ident, name in (("waiting-bot", "bot/main"), ("waiting-code", "code/architect")):
                gates[ident] = asyncio.Event()
                tasks.append(asyncio.create_task(request(name, ident)))
            key = str(backend_resource_key(shared))
            await wait_until(
                lambda: controller.snapshot(route_name="shared", max_concurrent=4, resource_key=key)["queued"] == 2
            )
            assert len(started) == 4
            # Cancelling a fifth/sixth pending client removes only its waiter,
            # without consuming a slot or starving the other service classes.
            gates["cancelled"] = asyncio.Event()
            cancelled = asyncio.create_task(request("bot/main", "cancelled"))
            await wait_until(
                lambda: controller.snapshot(route_name="shared", max_concurrent=4, resource_key=key)["queued"] == 3
            )
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert controller.snapshot(route_name="shared", max_concurrent=4, resource_key=key)["queued"] == 2
            gates["initial-0"].set()
            await wait_until(lambda: "waiting-code" in started)
            assert "waiting-bot" not in started
            gates["waiting-code"].set()
            await wait_until(lambda: "waiting-bot" in started)
            assert (
                registry.reservation_status(resource_key=key, owner_key="stranger", routing_priority=-100).state
                == "disabled_by_backend"
            )
            assert order[-2:] == ["waiting-code", "waiting-bot"]
        finally:
            for gate in gates.values():
                gate.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 4)
        assert controller.snapshot(route_name="shared", max_concurrent=4, resource_key=key)["active"] == 0

    asyncio.run(run())
