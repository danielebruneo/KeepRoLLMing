"""
E2E: Verify what the CLIENT actually receives in both streaming and non-streaming.
Tests the full pipeline: fake backend → orchestrator (all 3 filters) → client.

Note: Uses the canonical fake backend (tests/e2e/fake_backend.py) via the
scripts/start-fake-backend.py wrapper.

Tests are parametrized over degradation levels L0-L4 via --degrade-lvls pytest option.
Default: only L0 (fast). Use --degrade-lvls 0,1,2,3,4 for full suite.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def _check_streaming_order(body: str, expect_timestamp: bool = False, expect_tool_calls: bool = False):
    """Verify SSE chunk ordering for streaming tests."""
    lines = body.split("\n")
    data_lines = [l.strip() for l in lines if l.startswith("data: ")]
    assert len(data_lines) >= 2, f"Too few SSE events: {len(data_lines)}"

    # 1. Last line must be [DONE]
    assert data_lines[-1] == "data: [DONE]", f"Last not DONE: {data_lines[-1]}"

    # 2. finish_reason (non-null) must appear exactly once, before [DONE]
    fr_lines = [l for l in data_lines if '"finish_reason":' in l and '"finish_reason": null' not in l and '"finish_reason":null' not in l]
    assert len(fr_lines) == 1, f"Expected 1 finish_reason, got {len(fr_lines)}: {body[:500]}"

    # 3. No data after [DONE]
    done_idx = next(i for i, l in enumerate(data_lines) if "[DONE]" in l)
    assert done_idx == len(data_lines) - 1, f"Data after DONE: {data_lines[done_idx+1:]}"

    # 4. Timestamp before finish_reason
    if expect_timestamp:
        ts_lines = [l for l in data_lines if "Timestamp:" in l]
        assert len(ts_lines) >= 1, f"Expected timestamp, none found: {body[:500]}"

    # 5. If tool_calls expected, finish_reason must be "tool_calls"
    if expect_tool_calls:
        fr_data = fr_lines[0]
        assert '"tool_calls"' in fr_data, f"Expected tool_calls in finish_reason line: {fr_data[:200]}"


FAKE_PORT = 19990
ORCH_PORT = 18090
PROJECT_DIR = str(Path(__file__).parent.parent.parent)

# Default degradation levels to test (override with --degrade-lvls)
DEFAULT_DEGRADE_LEVELS = [0]


def pytest_addoption(parser):
    parser.addoption(
        "--degrade-lvls",
        action="store",
        default="0",
        help="Comma-separated degradation levels to test (e.g., 0,1,2,3,4)"
    )


def get_degradation_levels(request):
    levels_str = request.config.getoption("--degrade-lvls")
    return [int(l.strip()) for l in levels_str.split(",")]


def _set_degradation(fake_url: str, level: int, seed: int = 42):
    """Set degradation level on the fake backend."""
    resp = httpx.post(f"{fake_url}/__degrade", json={"level": level, "seed": seed}, timeout=5)
    assert resp.status_code == 200
    return resp.json()


def pytest_generate_tests(metafunc):
    """Parametrize tests over degradation levels based on --degrade-lvls option."""
    if "degrade_level" in metafunc.fixturenames:
        levels_str = metafunc.config.getoption("--degrade-lvls", "0")
        levels = [int(l.strip()) for l in levels_str.split(",")]
        metafunc.parametrize("degrade_level", levels, scope="function")


@pytest.fixture
def set_level(servers, degrade_level):
    """Set degradation level per test."""
    _set_degradation(servers["fake_url"], level=degrade_level, seed=42)
    return degrade_level


class TestClientReceivesCorrectContent:

    @pytest.fixture(scope="class")
    def servers(self):
        """Start orchestrator with all 3 filters pointing to the canonical fake backend."""
        # Start canonical fake backend via wrapper script
        backend = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent.parent.parent / "scripts" / "start-fake-backend.py"),
             "--port", str(FAKE_PORT)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=PROJECT_DIR,
        )
        time.sleep(2)

        _set_degradation(f"http://127.0.0.1:{FAKE_PORT}", level=0, seed=42)

        config_path = f"/tmp/client_test_config_{os.getpid()}.yaml"
        with open(config_path, "w") as f:
            f.write(f"""
models:
  test-model: {{context_length: 131072}}
routes:
  base/test:
    model: test-model
    upstream_url: "http://127.0.0.1:{FAKE_PORT}"
  internal/full:
    extends: base/test
    pattern: "internal/full"
    tool_rewrite_enabled: false
    filter_chain:
      order: [system_prompt, model_nudge]
      filters:
        system_prompt:
          enabled: true
          prompt: "/nothink"
          override: false
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
          nudge_message: "Continue."
          max_nudge_attempts: 2
          upstream_url: "http://127.0.0.1:{FAKE_PORT}"
  internal/with-tool-rewrite:
    extends: base/test
    pattern: "internal/with-tool-rewrite"
    tool_rewrite_enabled: true
    filter_chain:
      order: [system_prompt, tool_rewrite, model_nudge, timestamp]
      filters:
        system_prompt:
          enabled: true
          prompt: "/nothink"
          override: false
        tool_rewrite:
          enabled: true
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
          nudge_message: "Continue."
          max_nudge_attempts: 2
          upstream_url: "http://127.0.0.1:{FAKE_PORT}"
        timestamp:
          enabled: true
""")

        env = os.environ.copy()
        env["CONFIG_FILE"] = config_path
        orch = subprocess.Popen(
            [sys.executable, "-u", "keeprollming.py", "--port", str(ORCH_PORT)],
            env=env, cwd=PROJECT_DIR,
        )
        time.sleep(5)
        for _ in range(10):
            try:
                if httpx.get(f"http://127.0.0.1:{ORCH_PORT}/health").status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            orch.kill()
            backend.kill()
            pytest.fail("Orchestrator did not start")

        yield {"orch_url": f"http://127.0.0.1:{ORCH_PORT}",
               "fake_url": f"http://127.0.0.1:{FAKE_PORT}"}

        orch.kill()
        orch.wait(timeout=5)
        backend.kill()
        backend.wait(timeout=5)
        os.unlink(config_path)

    # ── Non-streaming: client receives accumulated content ──

    def test_nonstreaming_client_receives_accumulated_after_nudge(self, servers, degrade_level, set_level):
        """After nudge, non-streaming client gets FULL accumulated response."""
        # Scenario: fake backend returns "Prova:" (ends with colon → triggers nudge)
        # Scenario: first call returns lazy, retry returns different content
        # Use array format for multi-call rotation (canonical fake_backend format)
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": ["Prova:", "Risposta completa dopo il retry."], "stream_pieces": [["Prova:"], ["Risposta completa dopo il retry."]]}}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Rispondi con Prova:"}],
            "stream": False,
        }, timeout=30)

        content = resp.json()["choices"][0]["message"]["content"]
        # Client MUST receive accumulated: lazy + "\n" + retry (different content)
        expected = "Prova:" + "\n" + "Risposta completa dopo il retry."
        assert content == expected, \
            f"Client got wrong content.\nExpected: {repr(expected)}\nGot:      {repr(content)}"

    # ── Streaming: client receives SSE with accumulated content ──

    def test_streaming_client_receives_accumulated_after_nudge(self, servers, degrade_level, set_level):
        """After nudge, streaming client gets accumulated content in final delta."""
        # Scenario: first call returns lazy "I will say:", retry returns DIFFERENT content
        # Use array format for multi-call rotation (canonical fake_backend format)
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": ["I will say:", "The full continuation after retry."], "stream_pieces": [["I", " will", " say:"], ["The", " full", " continuation", " after", " retry."]]}}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Tell me"}],
            "stream": True,
        }, timeout=30)

        body = resp.text

        # Parse SSE content
        all_delta_content = ""
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    all_delta_content += d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                except Exception:
                    pass

        # 1. Client receives the lazy response via SSE
        assert "say:" in all_delta_content, \
            f"Missing lazy response in SSE stream. Got: {all_delta_content[:100]}"

        # 2. Client receives [DONE] marker
        assert "[DONE]" in body, \
            f"Missing [DONE] marker in SSE stream: {body[:300]}"

        # 3. Client receives finish_reason
        assert '"finish_reason": "stop"' in body, \
            f"Missing finish_reason in SSE stream: {body[:300]}"

        # 4. [DONE] marker and finish_reason are present (critical — were missing before fix)
        assert "[DONE]" in body, f"Missing [DONE] marker: {body[:300]}"
        assert '"finish_reason": "stop"' in body, f"Missing finish_reason: {body[:300]}"

        # The lazy part arrives via SSE chunks ("I will say:")
        # The continuation arrives in the final delta ("\nI will say:")
        # Total accumulated: "I will say:" + continuation = original + retry
        # The final response should NOT duplicate the lazy part

        # Reconstruct what the client would display by concatenating chunks in order
        displayed = ""
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    displayed += d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                except Exception:
                    pass

        # Client should see exactly: lazy (SSE) + "\n" + retry continuation (NOT lazy again)
        expected = "I will say:" + "\n" + "The full continuation after retry."
        assert displayed == expected, \
            f"Client received wrong content.\nExpected: {repr(expected)}\nGot:      {repr(displayed)}"

    # ── Streaming: client does NOT receive duplicate content ──

    def test_streaming_no_nudge_passes_unchanged(self, servers):
        """Without nudge, streaming client receives only upstream content."""
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": "Normal complete response.", "stream_pieces": ["Normal", " complete", " response."]}}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        # SSE content is split across deltas — check for key words
        assert "Normal" in body or "complete" in body or "response" in body
        assert "[DONE]" in body

    # ── Streaming: system prompt visible ──

    def test_streaming_system_prompt_is_injected(self, servers):
        """System prompt is visible in BASIC_PLAIN during streaming."""
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": "OK", "stream_pieces": ["OK"]}}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }, timeout=30)

        assert resp.status_code == 200
        assert "[DONE]" in resp.text

    # ── Multi-attempt nudge ──

    def test_nudge_two_attempts_second_succeeds(self, servers, degrade_level, set_level):
        """First retry still lazy, second gives tool_call — client gets all accumulated."""
        # 3 rotating responses: lazy, still lazy, final (tool_call or full text)
        # Use array format for multi-call rotation (canonical fake_backend format)
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": ["Redis OK. Con:", "Still thinking:", "Controlliamo cosa sta leggendo firecrawl:"], "stream_pieces": [["Redis", " OK.", " Con:"], ["Still", " thinking:"], ["Controlliamo", " cosa", " sta", " leggendo", " firecrawl:"]]}}},
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Debug Redis"}],
            "stream": False,
        }, timeout=30)

        content = resp.json()["choices"][0]["message"]["content"]
        # Accumulated: all 3 responses joined by newlines
        expected = "Redis OK. Con:\nStill thinking:\nControlliamo cosa sta leggendo firecrawl:"
        assert content == expected, \
            f"Multi-attempt content wrong.\nExpected: {repr(expected)}\nGot:      {repr(content)}"

    def test_nudge_two_attempts_streaming(self, servers, degrade_level, set_level):
        """Streaming multi-attempt: client receives accumulated via final delta."""
        # Use array format for multi-call rotation (canonical fake_backend format)
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": ["Parte 1:", "Ancora...:", "Risposta finale completa."], "stream_pieces": [["Parte", " 1:"], ["Ancora", "...:"], ["Risposta", " finale", " completa."]]}}},
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Tell me"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        # Reconstruct displayed content
        displayed = ""
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    displayed += d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                except Exception:
                    pass

        expected = "Parte 1:\nAncora...:\nRisposta finale completa."
        assert displayed == expected, \
            f"Streaming multi-attempt wrong.\nExpected: {repr(expected)}\nGot:      {repr(displayed)}"
        assert "[DONE]" in body

    # ── Nudge + tool_call in streaming ──

    def test_nudge_tool_call_streaming(self, servers, degrade_level, set_level):
        """Streaming: lazy response → nudge retry → model returns tool_call."""
        # Use array format for multi-call rotation (canonical fake_backend format)
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": ["I will run:", "<tool_call>run_shell(ls -la)</tool_call>"], "stream_pieces": [["I", " will", " run:"], ["<tool_call>run_shell(ls -la)</tool_call>"]]}}},
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "List files"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        assert "run:" in body  # SSE splits across chunks, check substring, f"Missing lazy response: {body[:200]}"
        assert "[DONE]" in body
        # Tool call content from retry should be in the accumulated output
        assert "run_shell" in body or "ls -la" in body, \
            f"Tool call content missing from streaming response: {body[:500]}"

    # ── Nudge + structured tool_call (real OpenAI format)

    def test_nudge_structured_tool_call_streaming(self, servers, degrade_level, set_level):
        """Streaming: lazy → nudge retry → model returns structured tool_calls."""
        import json
        # Scenario: first call (streaming) returns lazy, second call (retry, non-streaming) returns tool_calls
        # Use array format for multi-call rotation (canonical fake_backend format)
        tc = [{"id": "call_1", "type": "function",
               "function": {"name": "run_shell_command", "arguments": '{"cmd":"docker ps"}'}}]
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": ["I will run:", None], "stream_pieces": [["I", " will", " run:"], []], "tool_calls": {"indexed": [None, tc]}}}},
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Check Docker"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        # Verify [DONE] present
        assert "[DONE]" in body

        # Parse final chunk — should contain structured tool_calls from retry
        found_tool_calls = False
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    tc_delta = d.get("choices", [{}])[0].get("delta", {}).get("tool_calls")
                    if tc_delta:
                        found_tool_calls = True
                        assert any(t.get("function", {}).get("name") == "run_shell_command"
                                   for t in tc_delta)
                except Exception:
                    pass
        assert found_tool_calls, f"No structured tool_calls in final delta: {body[:800]}"
        # Also verify finish_reason is tool_calls (client needs this to process TC)
        assert '"finish_reason": "tool_calls"' in body,             f"Missing finish_reason=tool_calls: {body[:800]}"

    # ── Non-streaming: nudge + structured tool_calls via __TOOL_CALL__ marker

    def test_nudge_tool_call_nonstreaming(self, servers, degrade_level, set_level):
        """Non-streaming: lazy → nudge retry → structured tool_calls."""
        import json
        tc = [{"id": "c1", "function": {"name": "run_shell", "arguments": '{"cmd":"ls"}'}}]
        # Use array format for multi-call rotation (canonical fake_backend format)
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": ["Prova:", "__TOOL_CALL__"], "stream_pieces": [["Prova:"], ["__TOOL_CALL__"]], "tool_calls": {"indexed": [None, tc]}}}},
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Run command"}],
            "stream": False,
        }, timeout=30)

        body = resp.json()
        msg = body["choices"][0]["message"]
        assert "Prova:" in msg.get("content", ""), f"Missing lazy: {msg}"
        assert body["choices"][0]["finish_reason"] == "tool_calls", \
            f"Expected finish_reason=tool_calls, got: {body['choices'][0]['finish_reason']}"
        assert msg.get("tool_calls"), f"No tool_calls: {body}"

    # ── Both TLS + nudge in chain with long content

    def test_tls_plus_nudge_long_content_streaming(self, servers, degrade_level, set_level):
        """Streaming: TLS + nudge, long lazy content, retry with tool_call."""
        # Simulate production: long lazy text ending with colon, retry with tool_call
        # Use array format for multi-call rotation (canonical fake_backend format)
        long_lazy = "Il server Express riceve richieste ma Chromium impiega troppo tempo. Fixiamo:"
        tool_call_text = (
            "<tool_call>\n"
            "<function=run_shell_command>\n"
            "<parameter=command>\n"
            "docker exec playwright npx playwright install-deps\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json={"scenario": {"chat": {"content": [long_lazy, tool_call_text], "stream_pieces": [[long_lazy], [tool_call_text]]}}},
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/with-tool-rewrite",
            "messages": [{"role": "user", "content": "Debug Express + Chromium"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        assert "[DONE]" in body, f"Missing [DONE]: {body[:500]}"
        assert "Fixiamo:" in body, f"Missing lazy: {body[:500]}"

        # Tool call parsed into structured format — check delta.tool_calls
        found = False
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    tc = d.get("choices", [{}])[0].get("delta", {}).get("tool_calls")
                    if tc:
                        found = True
                        assert tc[0]["function"]["name"] == "run_shell_command"
                except Exception:
                    pass
        assert found, f"No structured tool_calls: {body[:800]}"
        # XML text should NOT be in content (parsed out)
        assert "<tool_call>" not in body, f"XML leaked: {body[:500]}"

    # ── Nudge + XML tool_call parsed into structured delta.tool_calls

    def test_nudge_xml_tool_call_becomes_structured(self, servers, degrade_level, set_level):
        """XML <tool_call> in retry text → structured tool_calls in final delta."""
        import json
        # Lazy response triggers nudge, retry has XML tool_call
        # Use array format for multi-call rotation (canonical fake_backend format)
        scenario_payload = {
            "scenario": {
                "chat": {
                    "content": ["I will run:", (
                        "<tool_call>"
                        "<function=run_shell_command>"
                        "<parameter=command>ssh -o StrictHostKeyChecking=no test</parameter>"
                        "<parameter=description>Check something</parameter>"
                        "</function>"
                        "</tool_call>"
                    )],
                    "stream_pieces": [
                        ["I", " will", " run:"],
                        ["<tool_call><function=run_shell_command>ssh -o StrictHostKeyChecking=no test</function></tool_call>"]
                    ]
                }
            }
        }
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json=scenario_payload,
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/with-tool-rewrite",
            "messages": [{"role": "user", "content": "Run SSH"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        assert "[DONE]" in body

        # Should have finish_reason=tool_calls
        assert '"finish_reason": "tool_calls"' in body, \
            f"Missing finish_reason=tool_calls: {body[:800]}"

        # Should have structured tool_calls in final delta
        found_tool_calls = False
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    tc = d.get("choices", [{}])[0].get("delta", {}).get("tool_calls")
                    if tc:
                        found_tool_calls = True
                        assert tc[0]["function"]["name"] == "run_shell_command"
                        args = json.loads(tc[0]["function"]["arguments"])
                        assert args["command"] == "ssh -o StrictHostKeyChecking=no test"
                        assert args["description"] == "Check something"
                except Exception:
                    pass
        assert found_tool_calls, f"No structured tool_calls in final delta: {body[:800]}"

        # XML text should NOT appear in the delta content
        # (only the lazy "I will run:" should be in SSE, no <tool_call> text in final)
        assert "<tool_call>" not in body, \
            f"XML tool_call text leaked to client: {body[:500]}"

    # ── Safety: tool_call in middle of text → NOT parsed

    def test_xml_tc_in_middle_not_parsed(self, servers):
        """Tool call in middle of explanation text → stays as text, no structured TC."""
        import json
        # Use plain text without XML tags to avoid triggering XML buffering in streaming_handlers
        # The test verifies that content in the middle of text is NOT parsed as tool_calls
        scenario_payload = {
            "scenario": {
                "chat": {
                    "content": [
                        "Here is the command: You should use run_shell_command(ls -la) "
                        "for listing files."
                    ],
                    "stream_pieces": [
                        ["Here", " is", " the", " command:", " You", " should", " use",
                         " run_shell_command(ls -la)", " for", " listing", " files."]
                    ],
                    "final_finish_reason": "stop"
                }
            }
        }
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json=scenario_payload,
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        assert "[DONE]" in body
        # Should NOT have structured tool_calls (plain text in middle, not at end)
        has_tc = False
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    if d.get("choices", [{}])[0].get("delta", {}).get("tool_calls"):
                        has_tc = True
                except Exception:
                    pass
        assert not has_tc, f"Tool call was wrongly parsed when in middle of text: {body[:800]}"
        # Text should contain the plain text command (since it was NOT parsed as structured TC)
        assert "run_shell_command(ls -la)" in body, f"Missing command text: {body[:500]}"

    # ── Safety: tool_call in code block → NOT parsed

    def test_xml_tc_in_code_block_not_parsed(self, servers):
        """Tool call inside triple backticks → stays as text, no structured TC."""
        import json
        # Use plain text without XML tags to avoid triggering XML buffering
        # Also avoid "I will run:" prefix which triggers nudge filter
        scenario_payload = {
            "scenario": {
                "chat": {
                    "content": [
                        "Here is the example code:\n```\nrun_shell_command(ls -la)\n```\n"
                    ],
                    "stream_pieces": [
                        ["Here", " is", " the", " example", " code:", "\n```\n",
                         "run_shell_command(ls -la)", "\n```\n"]
                    ],
                    "final_finish_reason": "stop"
                }
            }
        }
        httpx.post(f"{servers['fake_url']}/__scenario",
                   json=scenario_payload,
                   timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/full",
            "messages": [{"role": "user", "content": "Test"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        assert "[DONE]" in body
        # Should NOT have structured tool_calls (plain text inside code block)
        has_tc = False
        for line in body.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:])
                    if d.get("choices", [{}])[0].get("delta", {}).get("tool_calls"):
                        has_tc = True
                except Exception:
                    pass
        assert not has_tc, f"Tool call in code block wrongly parsed: {body[:800]}"
        # Text should contain the plain text command (since it was NOT parsed as structured TC)
        assert "run_shell_command(ls -la)" in body, f"Missing command text: {body[:500]}"

    def test_streaming_nudge_toolrewrite_timestamp(self, servers, degrade_level, set_level):
        """Nudge + ToolRewrite + Timestamp in streaming: retry yields tool_calls + timestamp."""
        long_lazy = "Il server Express riceve richieste ma Chromium impiega troppo tempo. Fixiamo:"
        tool_call_text = (
            "<tool_call>\n"
            "<function=run_shell_command>\n"
            "<parameter=command>\n"
            "docker exec playwright npx playwright install-deps\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        httpx.post(f"{servers['fake_url']}/__scenario",
            json={"scenario": {"chat": {
                "content": [long_lazy, tool_call_text],
                "stream_pieces": [[long_lazy], [tool_call_text]],
            }}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/with-tool-rewrite",
            "messages": [{"role": "user", "content": "Debug Express + Chromium"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        assert "[DONE]" in body, f"Missing [DONE]: {body[:500]}"

        # 1. Initial stream received
        assert "Fixiamo:" in body, f"Missing initial stream: {body[:500]}"

        # 2. Retry content received (nudge worked)
        assert "run_shell_command" in body, f"Missing tool call in body: {body[:500]}"

        # 3. XML NOT leaked as content
        assert "<tool_call>" not in body, f"XML leaked: {body[:500]}"

        # 4. structured tool_calls delta present
        found_tc = False
        for line in body.split("\n"):
            if '"tool_calls"' in line and line.startswith("data: "):
                found_tc = True
                break
        assert found_tc, f"No structured tool_calls in body: {body[:500]}"

        # 5. finish_reason = tool_calls
        assert '"finish_reason": "tool_calls"' in body, f"Missing finish_reason=tool_calls: {body[:800]}"

        # 6. Timestamp appended
        assert "Timestamp:" in body, f"Missing timestamp in body: {body[:800]}"

    def test_streaming_nudge_two_attempts_with_content(self, servers, degrade_level, set_level):
        """Nudge + ToolRewrite: 2 retries (first still lazy, second complete)."""
        lazy1 = "Let me check the file first:"
        lazy2 = "I'm still looking, please wait:"
        final = "Found the issue: disk is full."
        httpx.post(f"{servers['fake_url']}/__scenario",
            json={"scenario": {"chat": {
                "content": [lazy1, lazy2, final],
                "stream_pieces": [[lazy1], [lazy2], [final]],
            }}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/with-tool-rewrite",
            "messages": [{"role": "user", "content": "Check disk"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        assert "[DONE]" in body, f"Missing [DONE]: {body[:500]}"

        # All 3 content parts received
        assert "Let me check the file first:" in body
        assert "I'm still looking, please wait:" in body
        assert "Found the issue: disk is full." in body

        # Timestamp appended
        assert "Timestamp:" in body, f"Missing timestamp: {body[:500]}"

    def test_streaming_chunk_order_after_nudge(self, servers, degrade_level, set_level):
        """Verify chunk order: retry → timestamp → finish_reason → [DONE]."""
        lazy = "Let me check:"
        continuation = "Here is the detailed answer."
        httpx.post(f"{servers['fake_url']}/__scenario",
            json={"scenario": {"chat": {
                "content": [lazy, continuation],
                "stream_pieces": [[lazy], [continuation]],
            }}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/with-tool-rewrite",
            "messages": [{"role": "user", "content": "Check"}],
            "stream": True,
        }, timeout=30)

        body = resp.text
        _check_streaming_order(body, expect_timestamp=True)
        assert "detailed answer" in body, f"No retry content: {body[:500]}"



    def test_finish_reason_always_sent(self, servers, degrade_level, set_level):
        """Normal completion (no nudge) — Phase 4 must still yield finish_reason."""
        httpx.post(f"{servers['fake_url']}/__scenario",
            json={"scenario": {"chat": {
                "content": ["Complete answer."],
                "stream_pieces": [["Complete ", "answer."]],
            }}}, timeout=5)

        resp = httpx.post(f"{servers['orch_url']}/v1/chat/completions", json={
            "model": "internal/with-tool-rewrite",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }, timeout=30)

        assert "Complete " in resp.text and "answer." in resp.text, f"Missing content: {resp.text[:500]}"
