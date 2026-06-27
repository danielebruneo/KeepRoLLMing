"""E2E test for the daemon entry point (keeprollming.py + daemon.sh).

Verifies the full startup path: daemon.sh → keeprollming.py → uvicorn → health endpoint.
This catches missing entry points, broken imports, and daemon script regressions.
"""

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest


PROJECT_DIR = str(Path(__file__).resolve().parents[2])
DAEMON = str(Path(PROJECT_DIR) / "scripts" / "daemon.sh")
PORT = 18777
BASE_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def running_daemon():
    """Create a minimal config, start the daemon, yield, then stop."""
    config = """
models:
  test-model: {context_length: 8192}
routes:
  local/main:
    pattern: "local/main"
    model: "test-model"
    upstream_url: "http://127.0.0.1:19999"
"""
    config_path = f"/tmp/krm_daemon_test_config_{os.getpid()}.yaml"
    with open(config_path, "w") as f:
        f.write(config)

    env = os.environ.copy()
    env["CONFIG_FILE"] = config_path

    proc = subprocess.Popen(
        ["bash", DAEMON, "start", "--port", str(PORT)],
        cwd=PROJECT_DIR, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Wait up to 10s for health endpoint
    deadline = time.time() + 10
    ok = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=2)
            if r.status_code == 200:
                ok = True
                break
        except Exception:
            pass
        time.sleep(1)

    assert ok, f"Daemon did not start within 10s (check /var/log/keeprollming/)"
    yield

    # Stop daemon
    subprocess.run(
        ["bash", DAEMON, "stop", "--port", str(PORT), "--force"],
        cwd=PROJECT_DIR, env=env,
        capture_output=True,
    )
    os.unlink(config_path)


def test_daemon_health_check(running_daemon):
    """Daemon is running and health endpoint responds."""
    resp = httpx.get(f"{BASE_URL}/health", timeout=10)
    assert resp.status_code == 200


def test_daemon_models_endpoint(running_daemon):
    """Models endpoint returns at least one route."""
    resp = httpx.get(f"{BASE_URL}/v1/models", timeout=10)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
