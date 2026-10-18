"""Recovery through actual HTTP payloads, pipeline re-entry, and accounting."""

import asyncio
import copy

import httpx
import pytest

from keeprollming.filters.reasoning_loop_stopper.request import ReasoningLoopStopperFilter
from keeprollming.orchestrator.filter import FilterExecutionContext, StreamingResponse
from keeprollming.orchestrator.pipeline import Pipeline
from tests.filters.test_nonstream_filtered_response import _CALL, _REASONING, _request, _upstream_response

NAMES = ("reasoning_loop_stopper", "model_tool_loop_stopper")


def success(content="Recovered", calls=None, reasoning="New reasoning", reason=None):
    return {
        "model": "test-model",
        "choices": [
            {
                "message": {
                    "content": content,
                    "tool_calls": calls or [],
                    "reasoning": reasoning,
                },
                "finish_reason": reason or ("tool_calls" if calls else "stop"),
            }
        ],
        "usage": {"prompt_tokens": 110, "completion_tokens": 5, "total_tokens": 115},
    }


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("send_user", [False, True])
def test_recovery_keeps_payload_headers_timeout_and_valid_messages(monkeypatch, name, send_user):
    sent = []
    pipeline = Pipeline.from_route_config(
        {
            name: {
                "enabled": True,
                "max_retries": 2,
                "send_user_message": send_user,
                "rls_message" if name == NAMES[0] else "tls_message": "CUSTOM INTERVENTION",
                **({"nudge_message": "CUSTOM USER"} if name == NAMES[1] else {}),
            }
        }
    )
    params = {
        "tools": [{"type": "function", "function": {"name": "write_file", "parameters": {}}}],
        "max_tokens": 8192,
        "temperature": 0.2,
        "reasoning_effort": "low",
        "top_k": 10,
    }
    out, metrics, _ = _request(
        monkeypatch,
        [_upstream_response(), success()],
        pipeline,
        sent,
        params,
        {"Authorization": "Bearer test-only-key", "X-Upstream": "kept"},
        345,
    )
    assert out["choices"][0]["message"]["content"] == "Recovered"
    assert len(sent) == 2
    for request, payload in sent:
        assert all(payload[key] == value for key, value in params.items())
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert request.headers["X-Upstream"] == "kept"
        assert request.extensions["timeout"]["read"] == 345
    messages = sent[1][1]["messages"]
    assert (messages[-1]["role"] == "user") == send_user
    if name == NAMES[0]:
        assert messages[-2 if send_user else -1]["role"] == "assistant"
        if send_user:
            assert messages[-1]["content"] == "CUSTOM INTERVENTION"
    else:
        assistant = messages[-3 if send_user else -2]
        result = messages[-2 if send_user else -1]
        assert result["tool_call_id"] == assistant["tool_calls"][0]["id"]
        assert result["content"] == "CUSTOM INTERVENTION"
        if send_user:
            assert messages[-1]["content"] == "CUSTOM USER"
    assert metrics[-1]["upstream_attempts"] == 2
    assert metrics[-1]["upstream_prompt_tokens"] == 210
    assert metrics[-1]["upstream_completion_tokens"] == 25


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("budget", [0, 1, 2])
def test_repeated_retry_is_rechecked_and_budget_is_honored(monkeypatch, name, budget):
    sent = []
    p = Pipeline.from_route_config({name: {"enabled": True, "max_retries": budget}})
    out, metrics, _ = _request(monkeypatch, _upstream_response(), p, sent)
    assert len(sent) == 1 + budget
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["choices"][0]["message"]["content"]
    assert not out["choices"][0]["message"].get("tool_calls")
    assert metrics[-1]["recovery_count"] == budget


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "error", [{"choices": []}, {"error": {"message": "bad"}}, httpx.Response(503, json={"error": "down"})]
)
def test_invalid_or_failed_retry_is_explicit_terminal_fallback(monkeypatch, name, error):
    out, metrics, _ = _request(
        monkeypatch, [_upstream_response(), error], Pipeline.from_route_config({name: {"enabled": True}})
    )
    choice = out["choices"][0]
    assert choice["finish_reason"] == "stop" and choice["message"]["content"]
    assert not choice["message"].get("tool_calls")
    assert metrics[-1]["upstream_attempts"] == 2
    assert not metrics[-1]["usage_complete"]


@pytest.mark.parametrize("name", NAMES)
def test_retry_length_and_incomplete_call_remain_truncated(monkeypatch, name):
    call = copy.deepcopy(_CALL)
    call["function"]["arguments"] = '{"path":'
    out, _, _ = _request(
        monkeypatch,
        [_upstream_response(), success(calls=[call], reason="length")],
        Pipeline.from_route_config({name: {"enabled": True}}),
    )
    assert out["choices"][0]["finish_reason"] == "length"


def test_regeneration_reenters_rewrite_before_tls(monkeypatch):
    xml = "<tool_call><function=read_file><parameter=path>state.md</parameter></function></tool_call>"
    out, _, _ = _request(
        monkeypatch,
        [_upstream_response(), success(content=xml)],
        Pipeline.from_route_config(
            {
                "tool_rewrite": {"enabled": True},
                "reasoning_loop_stopper": {"enabled": True},
                "model_tool_loop_stopper": {"enabled": True},
            }
        ),
    )
    message = out["choices"][0]["message"]
    assert message["tool_calls"][0]["function"]["name"] == "read_file"
    assert "<tool_call>" not in message["content"]


def test_rewrite_disambiguates_same_reasoning_before_another_rls_retry(monkeypatch):
    xml = "<tool_call><function=read_file><parameter=path>state.md</parameter></function></tool_call>"
    sent = []
    out, _, _ = _request(
        monkeypatch,
        [_upstream_response(), success(content=xml, reasoning=_REASONING)],
        Pipeline.from_route_config({"tool_rewrite": {"enabled": True}, "reasoning_loop_stopper": {"enabled": True}}),
        sent,
    )
    assert len(sent) == 2
    assert out["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "read_file"


def test_nudge_budget_survives_pipeline_reentry(monkeypatch):
    sent = []
    out, _, _ = _request(
        monkeypatch,
        success("Still thinking:"),
        Pipeline.from_route_config(
            {
                "model_nudge": {"enabled": True, "max_attempts": 2, "trigger_patterns": [":$"]},
            }
        ),
        sent,
    )
    assert len(sent) == 3
    assert out["choices"][0]["message"]["content"].count("Still thinking:") == 3


def test_global_budget_bounds_large_per_filter_budget(monkeypatch):
    sent = []
    out, _, _ = _request(
        monkeypatch,
        _upstream_response(),
        Pipeline.from_route_config(
            {
                "model_tool_loop_stopper": {"enabled": True, "max_retries": 99},
            }
        ),
        sent,
    )
    assert len(sent) == 11
    assert out["choices"][0]["finish_reason"] == "stop"
    assert not out["choices"][0]["message"].get("tool_calls")


def test_global_nudge_cap_preserves_already_accumulated_content(monkeypatch):
    sent = []
    out, _, _ = _request(
        monkeypatch,
        success("Still thinking:"),
        Pipeline.from_route_config(
            {
                "model_nudge": {"enabled": True, "max_attempts": 99, "trigger_patterns": [":$"]},
            }
        ),
        sent,
    )
    assert len(sent) == 11
    assert out["choices"][0]["message"]["content"].count("Still thinking:") == 11


def test_nudge_generated_call_reenters_exhausted_tls(monkeypatch):
    sent = []
    out, _, _ = _request(
        monkeypatch,
        [_upstream_response(), success("Continue:"), _upstream_response()],
        Pipeline.from_route_config(
            {
                "model_tool_loop_stopper": {"enabled": True, "max_retries": 1},
                "model_nudge": {"enabled": True, "trigger_patterns": [":$"], "max_attempts": 1},
            }
        ),
        sent,
    )
    assert len(sent) == 3
    assert not out["choices"][0]["message"].get("tool_calls")
    assert out["choices"][0]["finish_reason"] == "stop"


def test_reasoning_has_no_global_cross_request_cache():
    async def run():
        f = ReasoningLoopStopperFilter({"enabled": True})
        for _ in range(2):
            context = FilterExecutionContext(
                upstream_payload={"messages": [{"role": "user", "content": "same prefix"}]}
            )
            response = StreamingResponse(content="ok", reasoning_content=_REASONING)
            assert await f.process_response(response, context) is response

    asyncio.run(run())


@pytest.mark.parametrize("name", NAMES)
def test_detection_threshold_independent_from_retry_budget(monkeypatch, name):
    sent = []
    out, _, _ = _request(
        monkeypatch,
        _upstream_response(),
        Pipeline.from_route_config(
            {
                name: {
                    "enabled": True,
                    "max_repeats": 2,
                    "max_retries": 5,
                }
            }
        ),
        sent,
    )
    assert len(sent) == 1  # Only one prior repetition is present.
    assert out["choices"][0]["message"]["tool_calls"]


def test_nonstream_filter_recovery_emits_basic_events(monkeypatch):
    from keeprollming import app

    events = []

    class Dispatcher:
        def emit(self, event):
            events.append(event)

    monkeypatch.setattr(app, "get_event_dispatcher", lambda: Dispatcher())
    _request(monkeypatch, [_upstream_response(), success()], Pipeline.from_route_config({NAMES[0]: {"enabled": True}}))
    recovery = [event for event in events if event.type == "filter.loop.recovery"]
    assert {event.data["outcome"] for event in recovery} == {"retry", "received"}
    assert all(event.level == "BASIC" for event in recovery)


@pytest.mark.parametrize("name", NAMES)
def test_retry_timeout_overrides_route_transport_timeout(monkeypatch, name):
    sent = []
    _request(
        monkeypatch,
        [_upstream_response(), success()],
        Pipeline.from_route_config(
            {
                name: {
                    "enabled": True,
                    "retry_timeout": 7,
                }
            }
        ),
        sent,
        request_timeout=345,
    )
    assert [request.extensions["timeout"]["read"] for request, _ in sent] == [345, 7]


@pytest.mark.parametrize("name", NAMES)
def test_timeout_and_cancellation_do_not_leak_calls(name):
    async def run(cancel):
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def blocked(payload, **options):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()

        pipeline = Pipeline.from_route_config({name: {"enabled": True, "retry_timeout": 0.01}})
        history = [
            {"role": "assistant", "reasoning_content": _REASONING, "tool_calls": [_CALL]},
            {"role": "tool", "tool_call_id": _CALL["id"], "content": "done"},
        ]
        response = StreamingResponse(model="test", tool_calls=[_CALL], reasoning_content=_REASONING)
        task = asyncio.create_task(
            pipeline.process_response(response, {"messages": history}, "cancel", "test", upstream_caller=blocked)
        )
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result.content and not result.tool_calls and result.finish_reason == "stop"
        assert closed.is_set()

    asyncio.run(run(False))
    asyncio.run(run(True))


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "settings",
    [
        {"max_retries": -1},
        {"max_repeats": -1},
        {"retry_timeout": 0},
        {"retry_timeout": float("inf")},
        {"retry_timeout": True},
    ],
)
def test_invalid_budgets_are_rejected_at_config_validation(name, settings):
    with pytest.raises(ValueError):
        Pipeline.from_route_config({name: {"enabled": True, **settings}})
