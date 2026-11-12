"""One budget contract for planning, overrides and outgoing payloads."""

from types import SimpleNamespace

import pytest
import yaml

from keeprollming import config
from keeprollming.endpoints import chat_completions as chat
from keeprollming.processing.output_budget import OutputTokenBudgetError, resolve_output_token_budget
from keeprollming.types import DefaultSettings, Route


@pytest.mark.parametrize(
    ("client", "overrides", "expected"),
    [
        (None, {}, 65384),
        (1000, {}, 1000),
        (None, {"max_tokens": 4096}, 4096),
        (1000, {"max_tokens": 4096}, 4096),
        (1, {}, 1),
    ],
)
def test_budget_precedence(client, overrides, expected):
    result = resolve_output_token_budget(
        client_max_tokens=client,
        default_max_tokens=65384,
        context_window=262144,
        safety_margin=128,
        overrides=overrides,
    )
    assert result.requested == result.maximum == expected


@pytest.mark.parametrize("remaining", [1, 17, 63, 100])
def test_clamp_never_invents_64_tokens(remaining):
    result = resolve_output_token_budget(
        client_max_tokens=1000,
        default_max_tokens=4096,
        context_window=1024,
        safety_margin=128,
        prompt_tokens=1024 - 128 - remaining,
    )
    assert result.requested == 1000
    assert result.maximum == remaining


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1024"])
def test_invalid_effective_budget_is_rejected(invalid):
    with pytest.raises(OutputTokenBudgetError, match="positive integer"):
        resolve_output_token_budget(
            client_max_tokens=invalid,
            default_max_tokens=4096,
            context_window=8192,
            safety_margin=128,
        )


@pytest.mark.parametrize("prompt_tokens", [896, 897, 2000])
def test_no_context_room_is_rejected(prompt_tokens):
    with pytest.raises(OutputTokenBudgetError, match="no room"):
        resolve_output_token_budget(
            client_max_tokens=None,
            default_max_tokens=4096,
            context_window=1024,
            safety_margin=128,
            prompt_tokens=prompt_tokens,
        )


@pytest.mark.parametrize(
    ("client", "route_overrides", "preserved", "expected"),
    [
        (None, {}, {}, 65384),
        (1000, {}, {}, 1000),
        (1000, {"max_tokens": 4096}, {}, 4096),
        (1000, {"max_tokens": 4096}, {"max_tokens": 8192}, 8192),
    ],
)
def test_summary_and_payload_use_same_effective_budget(monkeypatch, client, route_overrides, preserved, expected):
    route = Route(
        name="budget",
        pattern="budget",
        model="test",
        upstream_url="http://backend.invalid",
        ctx_len=262144,
        max_tokens=65384,
        overrides=route_overrides,
    )
    seen = []
    monkeypatch.setattr(chat, "should_summarise", lambda **kw: seen.append(kw) or SimpleNamespace(should=False))
    monkeypatch.setattr(chat, "_count_tokens_safe", lambda _: 50)
    messages = [{"role": "user", "content": "hello"}]
    payload = {"model": "budget", "messages": messages}
    if client is not None:
        payload["max_tokens"] = client
    ctx = chat._resolve_route_context(
        "budget",
        messages,
        payload,
        client,
        "budget-test",
        route=route,
        model="test",
        preserved_overrides=preserved,
    )
    # Even an enabled global completion default cannot overwrite client/route selection.
    monkeypatch.setattr(config, "DEFAULT_MAX_COMPLETION_TOKENS", 77)
    result = chat._build_upstream_payload(
        payload,
        "test",
        messages,
        ctx["route_plan"],
        ctx["ctx_eff"],
        "budget-test",
        preserved,
    )
    assert seen[0]["max_out"] == result["max_tokens"] == expected
    assert payload.get("max_tokens") == client  # Input and route settings stay unmodified.
    assert route.max_tokens == 65384


def test_payload_clamp_logs_effective_override_and_request_id(monkeypatch):
    seen = []
    monkeypatch.setattr(chat._exec, "emit_max_tokens_clamped", lambda **kw: seen.append(kw))
    payload = {"max_tokens": 4096}
    chat._clamp_max_tokens_upstream(payload, 8192, 1024, 880, "clamp-id")
    assert payload["max_tokens"] == 16
    assert seen[0]["req_id"] == "clamp-id"
    assert seen[0]["requested"] == 4096
    assert seen[0]["adjusted"] == 16


def test_summary_uses_current_root_defaults_not_stale_import(monkeypatch):
    monkeypatch.setattr(config, "DEFAULTS", DefaultSettings(ctx_len=262144, max_tokens=2345))
    monkeypatch.setattr(chat, "DEFAULTS", DefaultSettings(max_tokens=4096))
    seen = []
    monkeypatch.setattr(chat, "should_summarise", lambda **kw: seen.append(kw) or SimpleNamespace(should=False))
    route = Route(name="reload", pattern="reload", model="test", upstream_url="http://backend.invalid")
    ctx = chat._resolve_route_context("reload", [], {}, None, "reload-budget", route=route, model="test")
    assert ctx["resolved_max_tokens"] == seen[0]["max_out"] == 2345


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({}, 4096),
        ({"default_max_completion_tokens": 8192}, 8192),
        ({"max_tokens": 32768, "default_max_completion_tokens": 8192}, 32768),
    ],
)
def test_global_completion_setting_is_only_a_default_fallback(monkeypatch, tmp_path, settings, expected):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    monkeypatch.setenv("CONFIG_FILE", str(path))
    loaded = config.load_config()
    assert loaded["defaults"]["max_tokens"] == expected
    defaults = DefaultSettings(max_tokens=expected)
    assert config.resolve_route_settings(Route(name="x", pattern="x", max_tokens=65384), {}, defaults)[1] == 65384
