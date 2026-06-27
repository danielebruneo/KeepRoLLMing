"""
E2E Tests for Nudge Filter with Fake Backend (self-contained).

Starts fake backend + orchestrator in-process. No live server needed.
"""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PROJECT_DIR = str(Path(__file__).parent.parent.parent)


class TestNudgeRetryE2E:

    @pytest.fixture(scope="class")
    def servers(self):
        """Start fake backend + orchestrator with nudge filter."""
        fake_port = _free_port()
        orch_port = _free_port()
        fake_url = f"http://127.0.0.1:{fake_port}"
        orch_url = f"http://127.0.0.1:{orch_port}"

        # 1. Start fake backend
        fake_script = os.path.join(PROJECT_DIR, "scripts", "start-fake-backend.py")
        fb = subprocess.Popen(
            [sys.executable, fake_script, "--port", str(fake_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        assert httpx.get(f"{fake_url}/__health").status_code == 200

        # 2. Write config with nudge filter only
        config_path = f"/tmp/e2e_nudge_config_{os.getpid()}.yaml"
        with open(config_path, "w") as f:
            f.write(f"""
models:
  fake-model:
    context_length: 131072
routes:
  base/fake:
    model: fake-model
    upstream_url: "{fake_url}"
  local/nudge_test:
    extends: base/fake
    pattern: "local/nudge_test"
    filter_chain:
      order: [model_nudge]
      filters:
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
          nudge_message: "Continue."
          max_nudge_attempts: 3
          upstream_url: "{fake_url}"
""")

        # 3. Start orchestrator
        env = os.environ.copy()
        env["CONFIG_FILE"] = config_path
        env["LOG_MODE"] = "BASIC_PLAIN"
        orch = subprocess.Popen(
            [sys.executable, "-u", "keeprollming.py", "--port", str(orch_port)],
            env=env, cwd=PROJECT_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(5)
        for _ in range(10):
            try:
                if httpx.get(f"{orch_url}/health").status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            orch.kill()
            fb.kill()
            pytest.fail("Orchestrator did not start")

        yield {"fake_url": fake_url, "orch_url": orch_url}

        orch.kill()
        orch.wait(timeout=5)
        fb.kill()
        fb.wait(timeout=5)
        os.unlink(config_path)

    def _set_scenario(self, servers, content):
        """Set fake backend scenario and reset call counter."""
        fake_url = servers["fake_url"]
        payload = {"scenario": {"chat": {"content": [content], "stream_pieces": [[content]]}}}
        httpx.post(f"{fake_url}/__scenario", json=payload, timeout=5)

    def _request(self, servers, messages, stream=False):
        """Make a request to the orchestrator."""
        orch_url = servers["orch_url"]
        return httpx.post(
            f"{orch_url}/v1/chat/completions",
            json={"model": "local/nudge_test", "messages": messages, "stream": stream},
            timeout=30,
        )

    def _call_count(self, servers):
        """Number of chat calls made to fake backend."""
        fake_url = servers["fake_url"]
        stats = httpx.get(f"{fake_url}/__stats", timeout=5).json()
        return stats.get("calls_by_kind", {}).get("chat", 0)

    # ── Non-streaming Tests ──

    def test_single_nudge_retry(self, servers):
        """Lazy response triggers nudge retry (non-streaming)."""
        self._set_scenario(servers, "I will tell you now:")
        self._request(servers, [{"role": "user", "content": "Tell me something"}])
        assert self._call_count(servers) >= 2, f"Expected 2+ calls, got {self._call_count(servers)}"

    def test_multiple_consecutive_nudges(self, servers):
        """Nudge retries up to max_attempts (non-streaming)."""
        self._set_scenario(servers, "Still thinking:")
        self._request(servers, [{"role": "user", "content": "Keep going"}])
        count = self._call_count(servers)
        assert count >= 2, f"Expected multiple retries, got {count} calls"

    def test_max_attempts_reached(self, servers):
        """After max_attempts, filter gives up and returns accumulated content (non-streaming)."""
        self._set_scenario(servers, "Not done yet:")
        resp = self._request(servers, [{"role": "user", "content": "Finish"}])
        assert resp.status_code == 200
        count = self._call_count(servers)
        assert count == 4, f"Expected 4 calls (1 original + 3 retries), got {count}"

    # ── Streaming Tests ──

    def test_single_nudge_retry_streaming(self, servers):
        """Lazy response triggers nudge retry (streaming)."""
        self._set_scenario(servers, "I will tell you now:")
        self._request(servers, [{"role": "user", "content": "Tell me something"}], stream=True)
        assert self._call_count(servers) >= 2, f"Expected 2+ calls in streaming, got {self._call_count(servers)}"

    def test_multiple_consecutive_nudges_streaming(self, servers):
        """Nudge retries up to max_attempts (streaming)."""
        self._set_scenario(servers, "Still thinking:")
        self._request(servers, [{"role": "user", "content": "Keep going"}], stream=True)
        count = self._call_count(servers)
        assert count >= 2, f"Expected multiple retries in streaming, got {count} calls"

    def test_max_attempts_reached_streaming(self, servers):
        """After max_attempts, filter gives up (streaming)."""
        self._set_scenario(servers, "Not done yet:")
        self._request(servers, [{"role": "user", "content": "Finish"}], stream=True)
        count = self._call_count(servers)
        assert count >= 4, f"Expected at least 4 calls in streaming, got {count}"

    # ── Edge Cases ──

    def test_no_nudge_when_response_does_not_end_with_colon(self, servers):
        """Nudge does NOT trigger when response lacks colon ending."""
        self._set_scenario(servers, "This is a complete response.")
        self._request(servers, [{"role": "user", "content": "Hello"}])
        assert self._call_count(servers) == 1, "Nudge triggered on non-lazy response"

    def test_no_nudge_when_response_does_not_end_with_colon_streaming(self, servers):
        """Nudge does NOT trigger on normal response (streaming)."""
        self._set_scenario(servers, "A complete streaming response.")
        self._request(servers, [{"role": "user", "content": "Hello"}], stream=True)
        assert self._call_count(servers) == 1, "Nudge triggered on non-lazy streaming response"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
