"""Real runner contracts for loop recovery, not just finalizer decisions."""

import json

import pytest

from keeprollming.streaming.events import ReasoningTextDelta, ToolCallComplete
from keeprollming.streaming.finalizer_factory import build_finalizers
from keeprollming.streaming.runner import run_stream


def call(name="write_file", ident="previous", args='{"path":"a.md"}'):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": args}}


def history(calls=None):
    calls = calls or [call()]
    return [
        item
        for tc in calls
        for item in (
            {"role": "assistant", "content": None, "reasoning_content": "Same reasoning", "tool_calls": [tc]},
            {"role": "tool", "tool_call_id": tc["id"], "content": "done"},
        )
    ]


def frames(delta, reason="stop"):
    return iter(
        [
            ("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n").encode(),
            ("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": reason}]}) + "\n\n").encode(),
            b"data: [DONE]\n\n",
        ]
    )


def assert_valid_results(messages):
    pending = set()
    for message in messages:
        if message["role"] == "assistant":
            assert not pending
            pending.update(tc["id"] for tc in message.get("tool_calls") or [])
        elif message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
        else:
            assert not pending
    assert not pending


@pytest.mark.asyncio
@pytest.mark.parametrize("conservative", [False, True])
@pytest.mark.parametrize("send_user", [False, True])
async def test_tls_runner_retries_with_valid_messages_and_keeps_payload(conservative, send_user):
    messages = history()
    settings = {
        "enabled": True,
        "max_retries": 1,
        "send_user_message": send_user,
        "nudge_message": "CUSTOM USER",
        "tls_message": "CUSTOM TOOL",
    }
    if conservative:
        settings["ab_loop_detection"] = True
    finalizers = build_finalizers({"model_tool_loop_stopper": settings}, messages)
    payload = {
        "messages": messages,
        "model": "test-model",
        "stream": True,
        "tools": [{"type": "function", "function": {"name": "write_file"}}],
        "max_tokens": 7777,
        "reasoning_effort": "low",
    }
    sent = []

    def factory(retry):
        sent.append(retry)
        assert_valid_results(retry["messages"])
        assert all(retry[key] == value for key, value in payload.items() if key != "messages")
        assert (retry["messages"][-1]["role"] == "user") == send_user
        if send_user:
            assert retry["messages"][-1]["content"] == "CUSTOM USER"
        return frames({"content": "Recovered"})

    wire = b"".join(
        [
            part
            async for part in run_stream(
                frames({"tool_calls": [dict(call(ident="rejected"), index=0)]}, "tool_calls"),
                finalizers=finalizers,
                upstream_factory=factory,
                payload=payload,
            )
        ]
    ).decode()
    assert len(sent) == 1
    assert "Recovered" in wire and '"tool_calls": [' not in wire
    assert wire.count("data: [DONE]") == 1


@pytest.mark.parametrize("fuzzy", [True, False])
def test_historical_loop_does_not_block_a_new_tool_or_text(fuzzy):
    config = {"enabled": True, **({"fuzzy_threshold": 0.9} if fuzzy else {"ab_loop_detection": True})}
    for tool in (True, False):
        tls = next(
            f
            for f in build_finalizers({"model_tool_loop_stopper": config}, history([call(), call(ident="old2")]))
            if type(f).__name__ == "TLSFinalizer"
        )
        if tool:
            tls.process_event(ToolCallComplete(index=0, id="new", name="read_file", arguments_json='{"path":"b"}'))
        tls.finalize()
        assert tls.decision is None and not tls.recovery_exhausted


def test_rls_uses_last_reasoning_turn_calls_not_entire_history():
    messages = history([call(), call("read_file", "read")])
    rls = next(
        f
        for f in build_finalizers({"reasoning_loop_stopper": {"enabled": True}}, messages)
        if type(f).__name__ == "RLSFinalizer"
    )
    rls.process_event(ReasoningTextDelta(delta="Same reasoning"))
    rls.process_event(ToolCallComplete(index=0, id="new", name="read_file", arguments_json='{"path":"a.md"}'))
    rls.finalize()
    assert rls.decision is not None


@pytest.mark.parametrize("name", ["model_tool_loop_stopper", "reasoning_loop_stopper"])
def test_stream_retry_budget_and_detection_threshold_are_independent(name):
    f = next(
        f
        for f in build_finalizers({name: {"enabled": True, "max_retries": 0, "max_repeats": 2}}, history())
        if type(f).__name__ in {"RLSFinalizer", "TLSFinalizer"}
    )
    assert f.max_attempts == 0 and f.max_repeats == 2


@pytest.mark.asyncio
async def test_global_budget_exhaustion_never_releases_rejected_call():
    messages = history()

    def factory(payload):
        pytest.fail("Zero global retry budget must not call upstream")

    wire = b"".join(
        [
            part
            async for part in run_stream(
                frames({"tool_calls": [dict(call(ident="current"), index=0)]}, "tool_calls"),
                finalizers=build_finalizers({"model_tool_loop_stopper": {"enabled": True}}, messages),
                payload={"messages": messages},
                upstream_factory=factory,
                max_global_recovery_attempts=0,
            )
        ]
    ).decode()
    assert '"tool_calls": [' not in wire
    assert '"finish_reason": "stop"' in wire
    assert wire.count("data: [DONE]") == 1


@pytest.mark.asyncio
async def test_stream_recovery_timeout_is_transport_option_not_upstream_body():
    sent = []

    def factory(payload, *, request_timeout_override=None):
        sent.append(request_timeout_override)
        assert "retry_timeout" not in payload
        return frames({"content": "Recovered"})

    messages = history()
    output = [
        part
        async for part in run_stream(
            frames({"tool_calls": [dict(call(ident="now"), index=0)]}, "tool_calls"),
            finalizers=build_finalizers({"model_tool_loop_stopper": {"enabled": True, "retry_timeout": 17}}, messages),
            upstream_factory=factory,
            payload={"messages": messages},
        )
    ]
    assert sent == [17]
    assert b"Recovered" in b"".join(output)
