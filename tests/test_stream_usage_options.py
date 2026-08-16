"""Outgoing streaming requests ask providers to report final token usage."""

from keeprollming.endpoints.chat_completions import _build_upstream_payload
from keeprollming.core.config_types import RouteSettings
from keeprollming.routing import Route, RoutePlan


def _payload(payload: dict) -> dict:
    route = Route(name="test", pattern="test", model="upstream-model")
    plan = RoutePlan.compile(
        route=route,
        client_model="chat/main",
        model="upstream-model",
        settings=RouteSettings.from_route(route, "upstream-model"),
        context_window=4096,
        default_max_tokens=8192,
        upstream_url="http://upstream",
    )
    return _build_upstream_payload(
        payload=payload,
        upstream_model="upstream-model",
        repacked_messages=payload["messages"],
        route_plan=plan,
        max_tokens_req=None,
        ctx_eff=4096,
        req_id="test-request",
    )


def test_streaming_requests_default_to_final_usage():
    result = _payload({"model": "chat/main", "stream": True, "messages": []})
    assert result["stream_options"] == {"include_usage": True}


def test_explicit_client_usage_choice_is_preserved():
    result = _payload({
        "model": "chat/main",
        "stream": True,
        "stream_options": {"include_usage": False, "foo": "bar"},
        "messages": [],
    })
    assert result["stream_options"] == {"include_usage": False, "foo": "bar"}
