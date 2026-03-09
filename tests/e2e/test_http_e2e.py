from __future__ import annotations

from pathlib import Path

import httpx
import pytest

def resolve_perf_request_file(perf_dir: Path, client_model: str) -> Path:
    candidates = []
    upstream_model = client_model.replace("pass/", "")
    candidates.append(perf_dir / f"{upstream_model}.requests.yaml")

    for cand in candidates:
        if cand.exists():
            return cand

    matches = sorted(perf_dir.glob("*.requests.yaml"))
    if len(matches) == 1:
        return matches[0]

    raise AssertionError(
        f"Could not resolve performance log file in {perf_dir}. "
        f"Tried: {[str(c) for c in candidates]}; found: {[m.name for m in matches]}"
    )

@pytest.mark.e2e_fake
def test_e2e_summary_http_retry_reduced_chunking_recovers_with_error(
    backend_target,
    orchestrator_server,
    backend_client: httpx.Client,
    configure_fake_backend,
    get_fake_stats,
):
    configure_fake_backend(
        {
            "models": {
                "main-model": {"context_length": 280},
                "summary-model": {"context_length": 5000},
            },
            "summary": {
                "content": "summary after retry",
                "script": [
                    {"type": "error", "status": 500, "message": "temporary backend failure"},
                    {"content": "summary chunk 1 ok", "include_usage": True},
                    {"content": "summary chunk 2 ok", "include_usage": True},
                    {"content": "merged summary ok", "include_usage": True},
                ],
            },
            "chat": {"content": "response after retry summary", "include_usage": True},
        }
    )

    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": f"segmento {i} - " + ("X" * 360)})
        messages.append({"role": "assistant", "content": f"reply {i} - " + ("Y" * 340)})
    messages.append({"role": "user", "content": "chiudi il test"})

    resp = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": backend_target.client_model_summary,
            "messages": messages,
            "stream": False,
            "max_tokens": 64,
        },
        timeout=40.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == "response after retry summary"

    stats = get_fake_stats()
    summary_calls = stats["calls_by_kind"].get("summary", 0)
    assert 0 <= summary_calls <= 8
    stdout_text = orchestrator_server.stdout_path.read_text(encoding="utf-8", errors="replace")
    assert (
        "summary_no_progress_abort" in stdout_text
        or "summary_overflow_forced_split" in stdout_text
        or "summary_retry_exhausted" in stdout_text
        or "summary_overflow_chunking" in stdout_text
    )

@pytest.mark.e2e_fake
@pytest.mark.e2e_live
@pytest.mark.parametrize("backend_target", ["fake", "live"], indirect=True)
def test_e2e_nonstream_roundtrip_and_performance_logs(
    backend_target,
    orchestrator_server,
    backend_client: httpx.Client,
    configure_fake_backend,
):
    if backend_target.mode == "fake":
        configure_fake_backend(
            {
                "chat": {
                    "content": "E2E non-stream OK",
                    "include_usage": True,
                }
            }
        )

    resp = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": backend_target.client_model_basic,
            "messages": [{"role": "user", "content": "dimmi ciao dal test e2e"}],
            "stream": False,
            "max_tokens": 64,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["choices"][0]["message"]["content"]
    if backend_target.mode == "fake":
        assert data["choices"][0]["message"]["content"] == "E2E non-stream OK"

    
    req_file = resolve_perf_request_file(
        orchestrator_server.perf_dir,
        backend_target.client_model_basic,
    )
    assert req_file.exists()
    
    text = req_file.read_text(encoding="utf-8")
    assert "model: {}".format(backend_target.client_model_basic.replace("pass/","")) in text
    assert "stream: false" in text
    assert "tps:" in text

    summary_file = orchestrator_server.perf_dir / "summary.yaml"
    assert summary_file.exists()
    summary_text = summary_file.read_text(encoding="utf-8")
    assert "model: {}".format(backend_target.client_model_basic.replace("pass/","")) in summary_text
    assert "requests: 1" in summary_text


@pytest.mark.e2e_fake
@pytest.mark.e2e_live
@pytest.mark.parametrize("backend_target", ["fake", "live"], indirect=True)
def test_e2e_stream_roundtrip_and_live_metrics(
    backend_target,
    orchestrator_server,
    backend_client: httpx.Client,
    configure_fake_backend,
):
    if backend_target.mode == "fake":
        configure_fake_backend(
            {
                "chat": {
                    "content": "streaming e2e works",
                    "stream_pieces": ["streaming ", "e2e ", "works"],
                    "include_usage": False,
                    "ttft_ms": 80,
                    "chunk_delay_ms": 20,
                }
            }
        )

    with backend_client.stream(
        "POST",
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": backend_target.client_model_basic,
            "messages": [{"role": "user", "content": "fammi uno stream breve"}],
            "stream": True,
            "max_tokens": 64,
        },
    ) as resp:
        assert resp.status_code == 200, resp.text
        raw = b"".join(resp.iter_bytes())

    body = raw.decode("utf-8", errors="replace")
    assert "data:" in body
    assert "[DONE]" in body

    req_file = resolve_perf_request_file(
        orchestrator_server.perf_dir,
        backend_target.client_model_basic,
    )
    assert req_file.exists()
    text = req_file.read_text(encoding="utf-8")
    assert "stream: true" in text
    assert "ttft_ms:" in text
    assert "completion_tokens_source:" in text
    assert "tps:" in text


@pytest.mark.e2e_fake
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
def test_e2e_summary_overflow_chunking_recovers(
    backend_target,
    orchestrator_server,
    backend_client: httpx.Client,
    configure_fake_backend,
    get_fake_stats,
):
    configure_fake_backend(
        {
            "models": {
                "main-model": {"context_length": 260},
                "summary-model": {"context_length": 5000},
            },
            "summary": {
                "content": "chunked summary ok",
                "overflow_if_prompt_chars_gt": 2600,
                "include_usage": True,
            },
            "chat": {"content": "main response after summary", "include_usage": True},
        }
    )

    long_messages = []
    for i in range(12):
        long_messages.append({"role": "user", "content": f"messaggio utente {i} - " + ("A" * 340)})
        long_messages.append({"role": "assistant", "content": f"risposta assistente {i} - " + ("B" * 320)})
    long_messages.append({"role": "user", "content": "domanda finale"})

    resp = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": backend_target.client_model_summary,
            "messages": long_messages,
            "stream": False,
            "max_tokens": 64,
        },
        timeout=40.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == "main response after summary"

    stats = get_fake_stats()
    assert stats["calls_by_kind"].get("summary", 0) >= 2
    assert stats["calls_by_kind"].get("chat", 0) >= 1

    req_file = resolve_perf_request_file(
        orchestrator_server.perf_dir,
        backend_target.client_model_basic,
    )
    assert req_file.exists()
    text = req_file.read_text(encoding="utf-8")
    assert "did_summarize: true" in text


@pytest.mark.e2e_fake
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
def test_e2e_summary_http_retry_reduced_chunking_recovers(
    backend_target,
    orchestrator_server,
    backend_client: httpx.Client,
    configure_fake_backend,
    get_fake_stats,
):
    configure_fake_backend(
        {
            "models": {
                "main-model": {"context_length": 280},
                "summary-model": {"context_length": 5000},
            },
            "summary": {
                "content": "summary after retry",
                "script": [
                    {"type": "error", "status": 500, "message": "temporary backend failure"},
                    {"content": "summary chunk 1 ok", "include_usage": True},
                    {"content": "summary chunk 2 ok", "include_usage": True},
                    {"content": "merged summary ok", "include_usage": True},
                ],
            },
            "chat": {"content": "response after retry summary", "include_usage": True},
        }
    )

    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": f"segmento {i} - " + ("X" * 360)})
        messages.append({"role": "assistant", "content": f"reply {i} - " + ("Y" * 340)})
    messages.append({"role": "user", "content": "chiudi il test"})

    resp = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": backend_target.client_model_summary,
            "messages": messages,
            "stream": False,
            "max_tokens": 64,
        },
        timeout=40.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == "response after retry summary"

    stats = get_fake_stats()
    assert stats["calls_by_kind"].get("summary", 0) >= 2
    assert stats["calls_by_kind"].get("chat", 0) == 1

    req_file = resolve_perf_request_file(
        orchestrator_server.perf_dir,
        backend_target.client_model_basic,
    )
    assert req_file.exists()
    text = req_file.read_text(encoding="utf-8")
    assert "did_summarize: true" in text


@pytest.mark.e2e_fake
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
def test_e2e_summary_single_oversized_message_does_not_loop(
    backend_target,
    orchestrator_server,
    backend_client: httpx.Client,
    configure_fake_backend,
    get_fake_stats,
):
    configure_fake_backend(
        {
            "models": {
                "main-model": {"context_length": 240},
                "summary-model": {"context_length": 160},
            },
            "summary": {
                "overflow_if_prompt_chars_gt": 900,
                "overflow_message": "request exceeds the available context size",
                "content": "summary should eventually recover",
                "include_usage": True,
            },
            "chat": {"content": "response after guarded summary", "include_usage": True},
        }
    )

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "U" * 7000},
        {"role": "assistant", "content": "A" * 3000},
        {"role": "user", "content": "domanda finale"},
    ]

    resp = backend_client.post(
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": backend_target.client_model_summary,
            "messages": messages,
            "stream": False,
            "max_tokens": 64,
        },
        timeout=40.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["choices"][0]["message"]["content"] == "response after guarded summary"

    stats = get_fake_stats()
    summary_calls = stats["calls_by_kind"].get("summary", 0)
    assert 0 <= summary_calls <= 8
    stdout_text = orchestrator_server.stdout_path.read_text(encoding="utf-8", errors="replace")
    assert (
        "summary_no_progress_abort" in stdout_text
        or "summary_overflow_forced_split" in stdout_text
        or "summary_retry_exhausted" in stdout_text
        or "summary_overflow_chunking" in stdout_text
    )


@pytest.mark.e2e_fake
@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
def test_e2e_stream_abort_is_reported_cleanly(
    backend_target,
    orchestrator_server,
    backend_client: httpx.Client,
    configure_fake_backend,
):
    configure_fake_backend(
        {
            "chat": {
                "stream_pieces": ["partial ", "answer"],
                "include_usage": False,
                "abort_after_chunks": 1,
                "ttft_ms": 20,
            }
        }
    )

    with backend_client.stream(
        "POST",
        f"{orchestrator_server.base_url}/v1/chat/completions",
        json={
            "model": backend_target.client_model_basic,
            "messages": [{"role": "user", "content": "fammi fallire lo stream"}],
            "stream": True,
            "max_tokens": 64,
        },
        timeout=30.0,
    ) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode("utf-8", errors="replace")

    assert "partial " in body
    assert "Upstream stream exception" in body
    assert "[DONE]" in body
    stdout_text = orchestrator_server.stdout_path.read_text(encoding="utf-8", errors="replace")
    assert "upstream_stream_exception" in stdout_text
    assert "response_stream_reconstructed" in stdout_text
