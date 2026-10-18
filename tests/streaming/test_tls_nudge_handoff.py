"""Exhausted tool-loop replacement must reach continuation processing."""

import json

import pytest

from keeprollming.streaming.finalizer_factory import build_finalizers
from keeprollming.streaming.runner import run_stream


def frame(choice):
    return ("data: " + json.dumps({"choices": [choice]}) + "\n\n").encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_finish", [True, False])
@pytest.mark.parametrize("matching", [True, False])
@pytest.mark.parametrize("nudge_budget", [0, 1])
async def test_tls_fallback_reaches_nudge_before_terminal(explicit_finish, matching, nudge_budget):
    call = {
        "id": "call1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"a"}'},
    }
    history = [
        {"role": "assistant", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "call1", "content": "old result"},
    ]
    fallback = "Let me continue:" if matching else "Stopped repeated tool calls."
    finalizers = build_finalizers(
        {
            "model_tool_loop_stopper": {
                "enabled": True,
                "max_attempts": 1,
                "ab_loop_detection": True,
                "fallback_streaming_message": fallback,
            },
            "model_nudge": {
                "enabled": True,
                "trigger_patterns": [":$"],
                "max_attempts": nudge_budget,
            },
        },
        history,
    )

    def tool_stream():
        chunks = [frame({"delta": {"tool_calls": [dict(call, index=0)]}})]
        if explicit_finish:
            chunks.append(frame({"delta": {}, "finish_reason": "tool_calls"}))
        return iter([*chunks, b"data: [DONE]\n\n"])

    retries = []

    def upstream(payload):
        retries.append(payload)
        if len(retries) == 1:
            return tool_stream()
        assert payload["messages"][-2:] == [
            {"role": "assistant", "content": fallback},
            {"role": "user", "content": "Continue."},
        ]
        return iter(
            [
                frame({"delta": {"content": "Here is the answer."}}),
                frame({"delta": {}, "finish_reason": "stop"}),
                b"data: [DONE]\n\n",
            ]
        )

    output = [
        chunk
        async for chunk in run_stream(
            tool_stream(),
            finalizers=finalizers,
            upstream_factory=upstream,
            payload={"messages": history},
        )
    ]
    wire = b"".join(output).decode()
    rows = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    choices = [choice for row in rows for choice in row.get("choices", [])]
    text = "".join(choice.get("delta", {}).get("content", "") for choice in choices)
    continues = matching and nudge_budget > 0
    assert len(retries) == (2 if continues else 1)
    assert text == fallback + ("\nHere is the answer." if continues else "")
    assert [c["finish_reason"] for c in choices if c.get("finish_reason")] == ["stop"]
    assert wire.count("data: [DONE]") == 1
    assert not any(c.get("delta", {}).get("tool_calls") for c in choices)
