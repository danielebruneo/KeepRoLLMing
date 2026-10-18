"""P5 request capability/context eligibility contracts."""

from keeprollming.routing import Route, resolve_balance_selection
from keeprollming.routing.requirements import derive_request_requirements


def _route(name: str, *, capabilities=None, ctx_len=8192, max_tokens=1024):
    return Route(
        name=name,
        pattern=name,
        model=f"{name}-model",
        upstream_url=f"http://{name}:8000",
        capabilities=capabilities,
        ctx_len=ctx_len,
        max_tokens=max_tokens,
    )


def test_tools_request_excludes_target_without_declared_tool_capability():
    selector = Route(
        name="public/agent",
        pattern="public/agent",
        balance={"targets": ["runner/text", "runner/tools"]},
    )
    text = _route("runner/text", capabilities=["chat"])
    tools = _route("runner/tools", capabilities=["chat", "tools"])
    requirements = derive_request_requirements(
        {
            "messages": [{"role": "user", "content": "use a tool"}],
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        }
    )

    selection = resolve_balance_selection(
        selector,
        routes_by_name={route.name: route for route in (selector, text, tools)},
        is_eligible=lambda route: requirements.incompatibility_reason(route) is None,
    )

    assert requirements.required_capabilities == ("tools",)
    assert selection.selected_route.name == "runner/tools"


def test_capability_enforcement_can_be_disabled_without_disabling_context_fit():
    requirements = derive_request_requirements(
        {
            "messages": [{"role": "user", "content": "use a tool"}],
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        }
    )
    text_only = _route("runner/text", capabilities=["chat"])

    assert requirements.incompatibility_reason(text_only) == "missing_capability:tools"
    assert (
        requirements.incompatibility_reason(
            text_only,
            enforce_capabilities=False,
        )
        is None
    )


def test_image_and_reasoning_controls_require_vision_and_reasoning():
    requirements = derive_request_requirements(
        {
            "reasoning_effort": "high",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe this"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
                    ],
                }
            ],
        }
    )

    assert requirements.required_capabilities == ("reasoning", "vision")
    assert (
        requirements.incompatibility_reason(
            _route("runner/vision", capabilities=["chat", "vision"]),
        )
        == "missing_capability:reasoning"
    )
    assert (
        requirements.incompatibility_reason(
            _route("runner/all", capabilities=["chat", "vision", "reasoning"]),
        )
        is None
    )


def test_context_budget_excludes_smaller_target_and_preserves_larger_one():
    selector = Route(
        name="public/long",
        pattern="public/long",
        balance={"targets": ["runner/small", "runner/large"]},
    )
    small = _route("runner/small", capabilities=["chat"], ctx_len=300, max_tokens=128)
    large = _route("runner/large", capabilities=["chat"], ctx_len=2000, max_tokens=128)
    requirements = derive_request_requirements(
        {
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "x" * 800}],
        }
    )

    assert requirements.incompatibility_reason(small, safety_margin_tokens=64) == "context_insufficient"
    selection = resolve_balance_selection(
        selector,
        routes_by_name={route.name: route for route in (selector, small, large)},
        is_eligible=lambda route: (
            requirements.incompatibility_reason(
                route,
                safety_margin_tokens=64,
            )
            is None
        ),
    )
    assert selection.selected_route.name == "runner/large"


def test_disabled_reasoning_control_does_not_require_reasoning():
    requirements = derive_request_requirements(
        {
            "reasoning_effort": "none",
            "messages": [{"role": "user", "content": "ordinary chat"}],
        }
    )

    assert requirements.required_capabilities == ()


def test_route_overrides_contribute_to_effective_requirements_and_budget():
    requirements = derive_request_requirements(
        {
            "reasoning_effort": "none",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "ordinary chat"}],
        },
        route_overrides={"reasoning_effort": "medium", "max_tokens": 512},
    )

    assert requirements.required_capabilities == ("reasoning",)
    assert requirements.requested_completion_tokens == 512
