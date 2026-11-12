"""Current event envelope, request correlation and safe follow-mode contracts."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from keeprollming.observability.events_execution import emit_balance_decision, emit_http_in, emit_route_resolved
from keeprollming.observability.formatters import JsonFormatter, PlainTextFormatter

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "log-viewer.py"
spec = importlib.util.spec_from_file_location("krm_log_viewer", SCRIPT)
viewer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = viewer
spec.loader.exec_module(viewer)


def event(kind="execution.streaming.progress", req="a", **data):
    return {
        "type": kind,
        "req_id": req,
        "timestamp_ms": 1792140000123.5,
        "level": "BASIC",
        "source": {"domain": "execution", "component": "chat", "instance": None},
        "data": data,
    }


def request(req="a", route="public/test"):
    return event("execution.chat.http_in", req, client_model=route)


def backend(req="a", target="pool/fast", url="http://backend:7901", model="qwen"):
    return event(
        "execution.routing.balance_decision",
        req,
        requested_route="public/test",
        selected_route=target,
        selected_upstream_url=url,
        selected_model=model,
        alternatives=["pool/unused"],
    )


def test_render_real_envelope_with_shared_formatter():
    class Capture:
        def emit(self, captured):
            self.event = captured

    capture = Capture()
    emit_http_in("real-id", "public/test", stream=True, message_count=2, dispatcher=capture)
    record = viewer.decode_record(JsonFormatter().format(capture.event))
    text = viewer.render_plain(record)
    assert "[real-id]" in text and "model=public/test" in text
    assert "execution.http_in" in text and "streaming=true" in text
    assert "FORMAT_ERROR" not in text


def test_multiline_content_is_not_lost():
    text = viewer.render_plain(
        event("execution.chat.assistant", content="Hello\nWorld", reasoning_content="Think first")
    )
    assert "Hello" in text and "World" in text
    assert "REASONING:" in text and "ASSISTANT:" in text


def test_format_failure_is_visible_not_dropped():
    record = event("invalid")
    text = viewer.render_plain(record)
    assert "FORMAT_ERROR" in text and "invalid" in text


@pytest.mark.parametrize(
    "line",
    [
        "[]",
        "null",
        '{"msg":"obsolete"}',
        '{"type":"x.y","data":[]}',
        '{"type":"x.y","timestamp_ms":NaN}',
        '{"type":"x.y","req_id":7}',
    ],
)
def test_invalid_records_rejected(line):
    with pytest.raises(ValueError):
        viewer.decode_record(line)


def test_original_route_filters_interleaved_requests_and_flushes_preamble():
    selected = viewer.RequestFilter(routes=["public/*"])
    early = event("execution.chat.request_start")
    assert selected.feed(early) == []
    assert selected.feed(request("b", "bot/main")) == []
    assert selected.feed(request()) == [early, request()]
    assert selected.feed(event(req="b")) == []
    assert selected.feed(event()) == [event()]
    assert selected.feed(backend()) == [backend()]
    assert selected.feed(event(req=None)) == []


@pytest.mark.parametrize("pattern", ["pool/fast", "http://backend:7901", "*7901*", "qwen"])
def test_backend_matches_target_url_or_model_and_retains_early_records(pattern):
    selected = viewer.RequestFilter(backends=[pattern])
    assert selected.feed(request()) == []
    assert selected.feed(event()) == []
    assert selected.feed(backend()) == [request(), event(), backend()]
    assert selected.feed(event()) == [event()]


def test_filter_types_and_repeat_patterns_are_combined_correctly():
    selected = viewer.RequestFilter(
        routes=["bot/*", "public/*"], backends=["pool/*"], events=["execution.streaming.*", "execution.chat.assistant"]
    )
    assert selected.feed(request()) == []
    assert selected.feed(event()) == []
    assert selected.feed(backend()) == [event()]
    assert selected.feed(event("filter.loop.recovery")) == []
    assert selected.feed(event("execution.chat.assistant"))


def test_selected_target_is_not_original_route():
    selected = viewer.RequestFilter(routes=["pool/fast"])
    assert selected.feed(backend()) == []
    assert selected.feed(event("execution.chat.request_route", route="pool/fast")) == []


def test_fallback_selection_includes_both_sides_and_retains_later_events():
    selected = viewer.RequestFilter(routes=["public/test"], backends=["pool/slow"])
    preamble = [request(), backend(), event()]
    for record in preamble:
        assert selected.feed(record) == []
    fallback = event(
        "execution.chat.fallback",
        from_route="pool/fast",
        to_route="pool/slow",
        from_upstream_url="http://backend:7901",
        to_upstream_url="http://backend:7701",
    )
    assert selected.feed(fallback) == [*preamble, fallback]
    assert selected.feed(event()) == [event()]
    assert viewer.RequestFilter(backends=["pool/fast"]).feed(fallback) == [fallback]


def test_untried_alternatives_and_configured_fallbacks_never_match():
    selected = viewer.RequestFilter(backends=["pool/unused"])
    assert selected.feed(backend()) == []
    assert selected.feed(event("execution.chat.fallback_chain", attempts=[{"route": "pool/unused"}])) == []
    assert selected.feed(event("execution.chat.request_received", body_json={"model": "pool/unused"})) == []


def test_repeated_resolution_does_not_erase_identity():
    selected = viewer.RequestFilter(routes=["public/test"], backends=["qwen"])
    assert not selected.feed(request())
    resolved = event("execution.chat.route_resolved", resolved_route="pool/fast", upstream_model="qwen")
    assert selected.feed(resolved) == [request(), resolved]
    assert selected.feed(event("execution.chat.route_resolved", client_model="", upstream_model=""))
    assert selected.feed(event())


def test_direct_route_resolution_and_repacked_url():
    selected = viewer.RequestFilter(routes=["code/senior"], backends=["*7901*"])

    class Capture:
        def emit(self, value):
            self.event = value

    capture = Capture()
    emit_route_resolved(
        "a", "code/senior", "code/senior", "qwen", "qwen", "", False, 10000, 1000, [], dispatcher=capture
    )
    record = json.loads(JsonFormatter().format(capture.event))
    assert not selected.feed(record)
    repacked = event("execution.chat.repacked", upstream_url="http://backend:7901/v1/chat/completions")
    assert selected.feed(repacked) == [record, repacked]


def test_real_balance_event_fields_are_supported():
    class Capture:
        def emit(self, value):
            self.event = value

    capture = Capture()
    emit_balance_decision(
        "a",
        requested_route="public/test",
        selected_route="pool/fast",
        reroute_chain=[],
        alternatives=[],
        affinity_state="miss",
        selected_model="qwen",
        selected_upstream_url="http://backend:7901",
        selection_policy="least_loaded",
        selection_reason="least_loaded_eligible",
        candidate_loads={"pool/fast": {"active": 1, "capacity": 3, "queued": 0}},
        dispatcher=capture,
    )
    record = json.loads(JsonFormatter().format(capture.event))
    assert record["data"]["candidate_loads"]["pool/fast"]["active"] == 1
    plain = PlainTextFormatter().format(capture.event)
    assert 'policy="least_loaded"' in plain
    assert "load=[pool/fast:1/3]" in plain
    assert viewer.RequestFilter(routes=["public/test"], backends=["pool/fast"]).feed(record) == [record]


def test_event_only_mode_includes_system_events():
    record = event("execution.app.starting", req=None)
    assert viewer.RequestFilter(events=["execution.app.*"]).feed(record) == [record]


def test_request_id_filter_does_not_need_route_metadata():
    selected = viewer.RequestFilter(req_ids=["a", "b*"])
    for req in ("a", "b123"):
        record = event(req=req)
        assert selected.feed(record) == [record]
    assert not selected.feed(event(req="c"))
    assert not selected.feed(event(req=None))
    assert "c" not in selected.requests
    assert not viewer.RequestFilter(req_ids=["*"]).feed(event(req=None))


def test_request_id_combines_with_route_backend_and_event_filters():
    selected = viewer.RequestFilter(
        routes=["public/*"], backends=["pool/fast"], events=["execution.streaming.*"], req_ids=["a"]
    )
    assert not selected.feed(event())
    assert not selected.feed(request())
    assert selected.feed(backend()) == [event()]
    assert not selected.feed(backend("b"))
    assert not selected.feed(event("execution.chat.assistant"))


@pytest.mark.parametrize("option", ["--req-id", "--filter-req-id"])
def test_cli_request_id_filter(tmp_path, capsys, option):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(event("execution.chat.assistant", req=req, content=req)) for req in ("first", "second", "third")
        )
    )
    assert viewer.main(["--file", str(path), option, "first", option, "third"]) == 0
    output = capsys.readouterr().out
    assert "[first]" in output and "[third]" in output
    assert "[second]" not in output


def test_correlation_storage_is_bounded():
    selected = viewer.RequestFilter(routes=["missing"], max_requests=2, max_pending=2, max_bytes=100000)
    for req in ("a", "b", "c"):
        selected.feed(request(req))
    assert list(selected.requests) == ["b", "c"]
    assert len(selected.pending) == 2 and selected.dropped == 1
    tiny = viewer.RequestFilter(routes=["missing"], max_bytes=1)
    tiny.feed(request())
    assert not tiny.pending and tiny.pending_bytes == 0 and tiny.dropped == 1


def test_offline_accepts_final_line_without_newline(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("first\nlast", encoding="utf-8")
    assert list(viewer.read_lines(path)) == ["first\n", "last"]


def test_follow_handles_partial_unicode_rotation_and_truncation(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"old\n\xc3")
    steps = iter(("partial", "rotate", "truncate"))

    def tick(_):
        step = next(steps)
        if step == "partial":
            with path.open("ab") as stream:
                stream.write(b"\xa8\n")
        elif step == "rotate":
            path.rename(tmp_path / "events.jsonl.1")
            path.write_bytes(b"replacement\n")
        else:
            path.write_bytes(b"new\n")

    monkeypatch.setattr(viewer.time, "sleep", tick)
    lines = viewer.read_lines(path, follow=True)
    try:
        assert [next(lines) for _ in range(4)] == ["old\n", "è\n", "replacement\n", "new\n"]
    finally:
        lines.close()


def test_follow_waits_for_missing_replacement(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(viewer.time, "sleep", lambda _: path.write_text("ready\n"))
    lines = viewer.read_lines(path, follow=True)
    assert next(lines) == "ready\n"
    lines.close()


def test_cli_uses_current_events_and_keeps_relative_subdirectory(tmp_path):
    subdir = tmp_path / "nested"
    subdir.mkdir()
    path = subdir / "events.jsonl"
    records = [request(), event(), backend(), event("execution.chat.assistant", content="Hello Pawel")]
    path.write_text("not-json\n[]\n" + "\n".join(json.dumps(record) for record in records))
    env = dict(os.environ, LOG_PATH=str(tmp_path))
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--file",
            "nested/events.jsonl",
            "--route",
            "public/*",
            "--backend",
            "*7901*",
            "--filter-event",
            "execution.chat.assistant",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Hello Pawel" in result.stdout and "ASSISTANT:" in result.stdout
    assert "execution.http_in" not in result.stdout
    assert result.stderr.count("skipped invalid record") == 2
    assert "format_plain not available" not in result.stderr


@pytest.mark.parametrize("hardlink", [False, True])
def test_cli_refuses_to_overwrite_input(tmp_path, hardlink):
    path = tmp_path / "events.jsonl"
    content = json.dumps(request())
    path.write_text(content)
    output = tmp_path / "output.log" if hardlink else path
    if hardlink:
        os.link(path, output)
    with pytest.raises(SystemExit) as exc:
        viewer.main(["--file", str(path), "--plain-output", str(output)])
    assert exc.value.code == 2 and path.read_text() == content


def test_tail_primes_existing_request_without_replaying_history(tmp_path, monkeypatch, capsys):
    path = tmp_path / "events.jsonl"
    history = [request(), backend()]
    path.write_text("".join(json.dumps(record) + "\n" for record in history))

    def fake_lines(*args, **kwargs):
        yield from path.read_text().splitlines(keepends=True)
        yield json.dumps(event("execution.chat.assistant", content="LIVE ONLY")) + "\n"

    monkeypatch.setattr(viewer, "read_lines", fake_lines)
    assert viewer.main(["--file", str(path), "--tail", "--route", "public/test", "--backend", "pool/fast"]) == 0
    output = capsys.readouterr().out
    assert "LIVE ONLY" in output and "execution.http_in" not in output and "balance_decision" not in output
