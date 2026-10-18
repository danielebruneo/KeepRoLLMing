"""HTTP response contracts after non-streaming response filters intervene."""

import asyncio
import copy
import json
import time

import httpx
import pytest

from keeprollming.endpoints import chat_completions
from keeprollming.orchestrator.pipeline import Pipeline

_REASONING = "I will write the same file again."
_CALL = {
    "id": "call-current",
    "type": "function",
    "function": {"name": "write_file", "arguments": '{"path":"state.md","content":"done"}'},
}


def _upstream_response():
    return {
        "id": "chatcmpl-original",
        "object": "chat.completion",
        "created": 123,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "reasoning": _REASONING,
                    "reasoning_content": _REASONING,
                    "tool_calls": [copy.deepcopy(_CALL)],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


def _request(
    monkeypatch, upstream, pipeline, requests=None, payload_extra=None, route_headers=None, request_timeout=120
):
    metrics, transcripts = [], []
    monkeypatch.setattr(chat_completions, "_record_final_metrics", lambda m, **kw: metrics.append(m))
    monkeypatch.setattr(chat_completions._exec, "emit_assistant", lambda *a, **kw: transcripts.append((a, kw)))

    script = iter(upstream) if isinstance(upstream, list) else None

    def transport(request):
        if requests is not None:
            requests.append((request, json.loads(request.content)))
        body = next(script) if script else upstream
        return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await chat_completions.process_non_streaming_request(
                url="http://backend.test/v1/chat/completions",
                client=client,
                payload={
                    "model": "test-model",
                    "stream": False,
                    "messages": [
                        {"role": "user", "content": "Update state.md"},
                        {
                            "role": "assistant",
                            "content": None,
                            "reasoning_content": _REASONING,
                            "tool_calls": [dict(_CALL, id="call-previous")],
                        },
                        {"role": "tool", "tool_call_id": "call-previous", "content": "File written"},
                    ],
                    **(payload_extra or {}),
                },
                route_headers=route_headers or {},
                req_id="nonstream-filter-regression",
                upstream_model="test-model",
                fallback_attempts=[],
                t_start=time.perf_counter(),
                pipeline=pipeline,
                request_timeout=request_timeout,
            )

    response = asyncio.run(run())
    assert response.status_code == 200
    return json.loads(response.body), metrics, transcripts


@pytest.mark.parametrize("with_rls", [True, False], ids=["rls-before-tls", "tls-only"])
def test_suppressed_tool_call_never_reappears_in_http_response(monkeypatch, with_rls):
    """A failed intervention must not leak the original call back to the harness."""
    filters = {
        "model_tool_loop_stopper": {"enabled": True, "max_repeats": 1, "max_retries": 1},
    }
    if with_rls:
        filters["reasoning_loop_stopper"] = {"enabled": True, "max_repeats": 1, "max_retries": 1}

    result, metrics, transcripts = _request(monkeypatch, _upstream_response(), Pipeline.from_route_config(filters))
    choice = result["choices"][0]
    message = choice["message"]
    assert message["content"]
    assert not message.get("tool_calls"), "Suppressed upstream tool call was resurrected during serialization"
    assert choice["finish_reason"] == "stop"
    assert not message.get("reasoning_content")
    assert not message.get("reasoning")
    assert result["id"] == "chatcmpl-original"
    assert result["usage"] == _upstream_response()["usage"]
    assert metrics[-1]["finish_reason"] == "stop"
    assert transcripts[-1][1]["finish_reason"] == "stop"
    assert not transcripts[-1][1].get("tool_calls")
    assert metrics[-1]["upstream_attempts"] == 2
    assert metrics[-1]["recovery_count"] == 1


@pytest.mark.parametrize("tool_call", [True, False], ids=["new-call", "text-only"])
def test_rls_recovery_replaces_upstream_action_and_reasoning(monkeypatch, tool_call):
    new_call = copy.deepcopy(_CALL)
    new_call["id"] = "call-recovered"
    new_call["function"]["name"] = "read_file"
    new_reasoning = "I should inspect the file instead."

    retry_body = {
        "choices": [
            {
                "message": {
                    "content": "Trying another approach.",
                    "reasoning_content": new_reasoning,
                    "tool_calls": [new_call] if tool_call else [],
                },
                "finish_reason": "tool_calls" if tool_call else "stop",
            }
        ]
    }
    pipeline = Pipeline.from_route_config(
        {"reasoning_loop_stopper": {"enabled": True}, "model_tool_loop_stopper": {"enabled": True}}
    )
    result, _, _ = _request(monkeypatch, [_upstream_response(), retry_body], pipeline)
    choice = result["choices"][0]
    assert choice["message"].get("tool_calls", []) == ([new_call] if tool_call else [])
    assert choice["finish_reason"] == ("tool_calls" if tool_call else "stop")
    assert choice["message"]["reasoning_content"] == new_reasoning
    assert choice["message"]["reasoning"] == new_reasoning


@pytest.mark.parametrize("finish_reason", ["tool_calls", "length"])
def test_passed_through_tool_calls_keep_terminal_reason(monkeypatch, finish_reason):
    upstream = _upstream_response()
    upstream["choices"][0]["finish_reason"] = finish_reason
    result, _, _ = _request(monkeypatch, upstream, Pipeline([]))
    assert result["choices"][0]["message"]["tool_calls"] == [_CALL]
    assert result["choices"][0]["finish_reason"] == finish_reason


def test_unfiltered_response_is_preserved(monkeypatch):
    upstream = _upstream_response()
    result, _, _ = _request(monkeypatch, upstream, None)
    assert result == upstream
