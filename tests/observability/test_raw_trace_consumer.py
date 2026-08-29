"""Contract tests for opt-in exact-byte streaming trace capture."""

import base64
import hashlib
import json
import time

from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.formatters import JsonFormatter
from keeprollming.observability.raw_trace_consumer import RawTraceConsumer


def _event(event_type, req_id, data):
    return RuntimeEvent(
        type=event_type,
        source=EventSource(domain="transport", component="trace"),
        data=data,
        req_id=req_id,
        level="TRACE",
    )


def test_selected_route_persists_exact_ordered_chunks(tmp_path):
    consumer = RawTraceConsumer(
        policy="selected_routes", selected_routes=["chat/main"], base_dir=tmp_path,
    )
    req_id = "trace-1"
    consumer(_event("transport.trace.request_started", req_id, {"route": "chat/main"}))
    started = time.perf_counter_ns()
    consumer(_event("transport.trace.chunk", req_id, {
        "direction": "upstream", "boundary": "upstream.received", "chunk_index": 1,
        "monotonic_ns": started + 10, "relative_ns": 10, "raw_bytes": b"data: one\n\n",
    }))
    consumer(_event("transport.trace.chunk", req_id, {
        "direction": "downstream", "boundary": "pipeline.output", "chunk_index": 1,
        "monotonic_ns": started + 20, "relative_ns": 20, "raw_bytes": b"data: two\n\n",
    }))
    consumer(_event("transport.trace.lifecycle", req_id, {
        "boundary": "downstream.http_disconnect_observed",
        "monotonic_ns": started + 30,
        "peer": "192.168.8.150:40358",
    }))

    trace_path = next(tmp_path.rglob("trace.jsonl"))
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2, 3]
    assert [row["boundary"] for row in rows] == [
        "upstream.received",
        "pipeline.output",
        "downstream.http_disconnect_observed",
    ]
    assert [base64.b64decode(row["bytes_b64"]) for row in rows[:2]] == [
        b"data: one\n\n",
        b"data: two\n\n",
    ]
    assert rows[2]["kind"] == "lifecycle"
    assert rows[2]["data"]["peer"] == "192.168.8.150:40358"


def test_trace_is_bounded_per_request(tmp_path):
    consumer = RawTraceConsumer(policy="all", base_dir=tmp_path, max_bytes_per_request=3)
    req_id = "trace-2"
    consumer(_event("transport.trace.request_started", req_id, {"route": "chat/main"}))
    consumer(_event("transport.trace.chunk", req_id, {
        "direction": "upstream", "boundary": "upstream.received", "chunk_index": 1,
        "monotonic_ns": 1, "relative_ns": 1, "raw_bytes": b"four",
    }))
    trace_path = next(tmp_path.rglob("trace.jsonl"))
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert rows == [{"kind": "truncated", "max_bytes": 3}]


def test_lifecycle_before_route_resolution_is_flushed_when_trace_is_accepted(tmp_path):
    """ASGI response-start is retained although the body announces the route later."""
    consumer = RawTraceConsumer(
        policy="selected_routes", selected_routes=["chat/main"], base_dir=tmp_path,
    )
    req_id = "trace-pending-lifecycle"
    consumer(_event("transport.trace.lifecycle", req_id, {
        "boundary": "downstream.response_stream_started",
        "monotonic_ns": 1,
        "peer": "192.168.8.150:40358",
    }))
    consumer(_event("transport.trace.request_started", req_id, {"route": "chat/main"}))

    trace_path = next(tmp_path.rglob("trace.jsonl"))
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert rows[0]["sequence"] == 1
    assert rows[0]["boundary"] == "downstream.response_stream_started"


def test_generic_json_projection_redacts_raw_bytes():
    rendered = JsonFormatter().format(_event("transport.trace.chunk", "trace-3", {
        "raw_bytes": b"secret SSE payload", "chunk_index": 1,
    }))
    row = json.loads(rendered)
    assert row["data"]["raw_bytes"] == {
        "_binary_omitted": True,
        "byte_length": len(b"secret SSE payload"),
        "sha256": hashlib.sha256(b"secret SSE payload").hexdigest(),
    }
