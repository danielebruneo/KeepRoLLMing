"""Runtime acceptance tests for the default observability projectors."""

from __future__ import annotations

import json
import time

import httpx


def _wait_for(path, timeout: float = 3.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists() and path.read_text(encoding="utf-8", errors="replace"):
            return path.read_text(encoding="utf-8", errors="replace")
        time.sleep(0.05)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def test_streaming_transcript_and_projector_contract(
    orchestrator_server, configure_fake_backend, backend_client
):
    """A real server process produces readable PLAIN and canonical JSONL."""
    configure_fake_backend({
        "chat": {
            "stream_pieces": ["Assistant ", "response.", " More output."],
            "reasoning_pieces": ["Reasoning visible in the transcript."],
            "include_usage": True,
            "chunk_delay_ms": 25,
        }
    })

    with httpx.stream(
        "POST", f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "internal/timestamp-v2",
            "stream": True,
            "messages": [
                {"role": "system", "content": "System text must be visible."},
                {"role": "user", "content": "User text must be visible."},
            ],
        }, timeout=15.0,
    ) as response:
        assert response.status_code == 200
        assert "Assistant response." in "".join(response.iter_text())

    log_dir = orchestrator_server.workdir.parent / "logs"
    # The subprocess fixture supplies LOG_PATH below its per-test tmp directory.
    log_dir = orchestrator_server.perf_dir.parent / "logs"
    plain = _wait_for(log_dir / "keeprollming.log")
    jsonl = _wait_for(log_dir / "keeprollming.log.json")
    server = _wait_for(log_dir / "server.log")
    stdout = _wait_for(orchestrator_server.stdout_path)

    for transcript in (plain, stdout):
        assert "SYSTEM:" in transcript
        assert "System text must be visible." in transcript
        assert "USER:" in transcript
        assert "User text must be visible." in transcript
        assert "REASONING:" in transcript
        assert "Reasoning visible in the transcript." in transcript
        assert "ASSISTANT:" in transcript
        assert "Assistant response." in transcript
        assert "USAGE" in transcript
        assert "execution.streaming.progress" in transcript
        assert "body_json" not in transcript

    assert "decode_tps_est=" in plain

    events = [json.loads(line) for line in jsonl.splitlines() if line.strip()]
    assert events and all("type" in event and "timestamp_ms" in event for event in events)
    assert not any("msg" in event and "ts" in event for event in events)
    assert not any("bytes_b64" in json.dumps(event) for event in events)
    assert "execution.pipeline" not in server
    assert len([line for line in server.splitlines() if line.strip()]) == 1
    assert "path=/v1/chat/completions" in server
    assert "upstream_attempts=1" in server

    deadline = time.time() + 3.0
    perf_files = list(orchestrator_server.perf_dir.glob("*.requests.jsonl"))
    while not perf_files and time.time() < deadline:
        time.sleep(0.05)
        perf_files = list(orchestrator_server.perf_dir.glob("*.requests.jsonl"))
    assert perf_files
    perf_rows = [
        json.loads(line) for line in perf_files[0].read_text().splitlines() if line.strip()
    ]
    assert perf_rows[-1]["ttft_ms"] is not None
    assert perf_rows[-1]["ttft_ms"] > 0
    assert perf_rows[-1]["completed_at"] > 0
    assert perf_rows[-1]["completion_tokens_source"] == "client_visible_estimate"
    assert perf_rows[-1]["completion_tokens"] is not None
    assert perf_rows[-1]["upstream_completion_tokens"] is not None

    trace_files = list((log_dir / "raw_trace").rglob("trace.jsonl"))
    assert len(trace_files) == 1
    trace_rows = [json.loads(line) for line in trace_files[0].read_text().splitlines()]
    assert trace_rows
    assert [row["sequence"] for row in trace_rows] == list(range(1, len(trace_rows) + 1))
    assert {row["direction"] for row in trace_rows} == {"upstream", "downstream"}
    assert all("bytes_b64" in row and "relative_ns" in row for row in trace_rows)


def test_streaming_sends_reasoning_before_tool_call_and_terminal_finish(
    orchestrator_server, configure_fake_backend
):
    """The client receives a tool turn in the OpenAI SSE semantic order.

    A client must see the model's reasoning and its structured tool call before
    the terminal ``tool_calls`` finish.  The tool result belongs to the next
    client request, after the client has executed that call.
    """
    configure_fake_backend({
        "chat": {
            "stream_pieces": [],
            "reasoning_pieces": ["I need to run date first."],
            "tool_calls": [{
                "index": 0,
                "id": "call-date",
                "type": "function",
                "function": {
                    "name": "bash_tool",
                    "arguments": '{"command":"date"}',
                },
            }],
            "include_usage": True,
        }
    })

    with httpx.stream(
        "POST", f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "internal/timestamp-v2",
            "stream": True,
            "messages": [{"role": "user", "content": "What time is it?"}],
        }, timeout=15.0,
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert body.rstrip().endswith("data: [DONE]")

    def event_index(predicate):
        return next(index for index, event in enumerate(events) if predicate(event))

    reasoning_index = event_index(
        lambda event: "reasoning_content" in event["choices"][0]["delta"]
    )
    tool_call_index = event_index(
        lambda event: "tool_calls" in event["choices"][0]["delta"]
    )
    terminal_index = event_index(
        lambda event: event["choices"][0].get("finish_reason") == "tool_calls"
    )

    assert reasoning_index < tool_call_index < terminal_index


def test_direct_upstream_streaming_also_emits_assistant_and_usage(
    orchestrator_server, configure_fake_backend
):
    """Routes without a filter chain retain the same log transcript contract."""
    configure_fake_backend({
        "chat": {
            "stream_pieces": ["Direct assistant response."],
            "reasoning_pieces": ["Direct reasoning."],
            "include_usage": True,
        }
    })

    with httpx.stream(
        "POST", f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": "pass/main-model",
            "stream": True,
            "messages": [{"role": "user", "content": "Direct user text."}],
        }, timeout=15.0,
    ) as response:
        assert response.status_code == 200
        assert "Direct assistant response." in "".join(response.iter_text())

    log_dir = orchestrator_server.perf_dir.parent / "logs"
    plain = _wait_for(log_dir / "keeprollming.log")
    stdout = _wait_for(orchestrator_server.stdout_path)
    server = _wait_for(log_dir / "server.log")

    for transcript in (plain, stdout):
        assert "USER:" in transcript
        assert "Direct user text." in transcript
        assert "REASONING:" in transcript
        assert "Direct reasoning." in transcript
        assert "ASSISTANT:" in transcript
        assert "Direct assistant response." in transcript
        assert "USAGE" in transcript

    assert "upstream_attempts=1" in server
