"""Privacy-mode projections must not persist transcript-like content."""

import json

from keeprollming.observability.body_capture_consumer import BodyCaptureConsumer
from keeprollming.observability.default_projectors import _RedactingFormatter
from keeprollming.observability.events import EventSource, RuntimeEvent
from keeprollming.observability.formatters import JsonFormatter, PlainTextFormatter
from keeprollming.observability.raw_trace_consumer import RawTraceConsumer
from keeprollming.observability.redactor import ZeroContentRedactor
from keeprollming.observability.request_capture_consumer import RequestCaptureConsumer

SENTINEL = "ultra-secret-transcript-sentinel"


def _event(event_type: str, data: dict) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        timestamp_ns=1,
        source=EventSource(domain="execution", component="chat"),
        data=data,
        req_id="privacy-test",
        level="INFO",
    )


def test_plain_and_json_projections_omit_content_but_keep_route() -> None:
    event = _event(
        "execution.chat.conversation",
        {
            "route": "chat/private",
            "role": "user",
            "text": SENTINEL,
            "request_payload": {"messages": [{"content": SENTINEL}]},
        },
    )
    redactor = ZeroContentRedactor()

    plain = _RedactingFormatter(PlainTextFormatter(), redactor).format(event)
    structured = _RedactingFormatter(JsonFormatter(), redactor).format(event)

    assert SENTINEL not in plain
    assert SENTINEL not in structured
    assert "chat/private" in structured


def test_raw_trace_privacy_mode_records_chunk_metadata_without_bytes(tmp_path) -> None:
    trace = RawTraceConsumer(policy="all", base_dir=tmp_path, privacy_mode=True)
    trace(_event("transport.trace.request_started", {"route": "chat/private"}))
    trace(
        _event(
            "transport.trace.chunk",
            {
                "direction": "upstream",
                "boundary": "upstream.received",
                "chunk_index": 1,
                "raw_bytes": SENTINEL.encode(),
            },
        )
    )

    path = next(tmp_path.rglob("trace.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1]["kind"] == "chunk_omitted"
    assert rows[-1]["byte_length"] == len(SENTINEL)
    assert SENTINEL not in path.read_text()


def test_capture_consumers_omit_content_with_zero_content_redactor(tmp_path) -> None:
    redactor = ZeroContentRedactor()
    request_capture = RequestCaptureConsumer(
        base_dir=tmp_path / "requests",
        policy="all",
        redactor=redactor,
    )
    request_capture(
        _event(
            "request.capture.raw_inbound",
            {
                "raw_body": {"messages": [{"role": "user", "content": SENTINEL}]},
                "resolved_route": "chat/private",
                "upstream_model": "qwen",
            },
        )
    )
    body_capture = BodyCaptureConsumer(
        base_dir=tmp_path / "errors",
        policy="errors_only",
        redactor=redactor,
    )
    body_capture(
        _event(
            "execution.chat.upstream_error",
            {
                "route": "chat/private",
                "body": {"error": {"message": SENTINEL}},
                "request_payload": {"messages": [{"content": SENTINEL}]},
            },
        )
    )

    persisted = "\n".join(path.read_text() for path in tmp_path.rglob("*.json"))
    assert SENTINEL not in persisted
    assert "chat/private" in persisted
