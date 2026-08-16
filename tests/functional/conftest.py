"""
Functional test fixtures — fake backend + orchestrator with full filter chain.

These tests verify actual client behavior and log output against
a running orchestrator with a deterministic fake backend.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_DIR = str(Path(__file__).parent.parent.parent)


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FunctionalHarness:
    """Manages fake backend + orchestrator lifecycle and provides test helpers."""

    def __init__(self, fake_port: int, orch_port: int, extra_config: str = ""):
        self.fake_port = fake_port
        self.orch_port = orch_port
        self.fake_base = f"http://127.0.0.1:{fake_port}"
        self.orch_base = f"http://127.0.0.1:{orch_port}"
        self._backend = None
        self._orchestrator = None
        self._config_path = f"/tmp/func_test_{os.getpid()}.yaml"
        self._log_dir = f"/tmp/func_logs_{os.getpid()}"
        self._extra_config = extra_config

    def start(self):
        """Start fake backend and orchestrator with filter chain config."""
        os.makedirs(self._log_dir, exist_ok=True)

        # Start fake backend
        self._backend = subprocess.Popen(
            [sys.executable, str(Path(PROJECT_DIR) / "scripts" / "start-fake-backend.py"),
             "--port", str(self.fake_port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2)

        # Write orchestrator config
        config = f"""
models:
  test-model:
    context_length: 131072
routes:
  base/test:
    model: test-model
    upstream_url: "http://127.0.0.1:{self.fake_port}"
  internal/full:
    extends: base/test
    pattern: "internal/full"
    filters:
        system_prompt:
          enabled: true
          prompt: "/nothink"
          override: false
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
          nudge_message: "Continue."
          max_attempts: 2
          upstream_url: "http://127.0.0.1:{self.fake_port}"
{self._extra_config}
"""
        with open(self._config_path, "w") as f:
            f.write(config)

        env = os.environ.copy()
        env["CONFIG_FILE"] = self._config_path
        env["LOG_PATH"] = self._log_dir

        self._orchestrator = subprocess.Popen(
            [sys.executable, "-u", str(Path(PROJECT_DIR) / "keeprollming.py"),
             "--port", str(self.orch_port)],
            env=env, cwd=PROJECT_DIR,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(5)

    def stop(self):
        """Stop both processes."""
        for proc in [self._orchestrator, self._backend]:
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)

    # ── Client helpers ────────────────────────────────────────────────

    def post(self, model: str = "internal/full", messages: list = None,
             stream: bool = False, **kwargs):
        """Make a chat completion request and return response."""
        payload = {"model": model, "messages": messages or [
            {"role": "user", "content": "hello"}
        ], "stream": stream, **kwargs}
        return httpx.post(f"{self.orch_base}/v1/chat/completions",
                          json=payload, timeout=30)

    def post_stream(self, model: str = "internal/full", messages: list = None,
                    **kwargs):
        """Make a streaming request and collect SSE chunks."""
        return self.post(model=model, messages=messages, stream=True, **kwargs)

    # ── Fake backend control ───────────────────────────────────────────

    def set_scenario(self, **scenario):
        """Set fake backend scenario for next request.

        Supports both:
        - Old flat format: set_scenario(chat_content="text")
        - New structured format: set_scenario(scenario={"chat": {"content": [...], ...}})
        """
        # Convert old flat format to new structured format
        if "chat_content" in scenario and "scenario" not in scenario:
            payload = {"scenario": {"chat": {"content": [scenario["chat_content"]], "stream_pieces": [[scenario["chat_content"]]]}}}
        elif "scenario" in scenario:
            payload = {"scenario": scenario["scenario"]}
        else:
            payload = {"scenario": scenario}
        httpx.post(f"{self.fake_base}/__scenario", json=payload, timeout=5)

    # ── Log helpers ────────────────────────────────────────────────────

    @property
    def log_path(self):
        """Path to JSON log (if enabled)."""
        return Path(self._log_dir) / "keeprollming.log.json"

    @property
    def plain_log_path(self):
        """Path to plain log."""
        return Path(self._log_dir) / "keeprollming.log"

    def read_log_json(self):
        """Read JSON log as list of events.

        The orchestrator runs in a subprocess with an async log writer that
        flushes periodically (every 1 s).  Wait briefly to ensure records are
        on disk before reading.
        """
        import time
        time.sleep(1.2)  # allow the async writer's periodic flush to fire

        path = self.log_path
        if not path.exists():
            return []
        events = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    def read_plain_log(self):
        """Read plain text log."""
        path = self.plain_log_path
        if not path.exists():
            return ""
        return path.read_text()


@pytest.fixture(scope="class")
def harness():
    """Class-scoped fixture: one backend + orchestrator per test class."""
    fake_port = _free_port()
    orch_port = _free_port()
    h = FunctionalHarness(fake_port, orch_port)
    h.start()
    yield h
    h.stop()


@pytest.fixture(scope="class")
def broken_harness():
    """Harness with NO upstream URL configured — verifies error responses."""
    fake_port = _free_port()
    orch_port = _free_port()
    # Deliberately pass no fake backend URL — simulates misconfiguration
    h = FunctionalHarness(fake_port, orch_port, extra_config="""
  internal/broken:
    pattern: "internal/broken"
    model: test-model
    # Deliberately no upstream_url — verifies error handling
    summary_enabled: false
""")
    h.start()
    yield h
    h.stop()
