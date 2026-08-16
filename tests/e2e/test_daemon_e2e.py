"""E2E test for the root lifecycle launcher (krm + keeprollming.py).

Verifies the full startup path: krm → keeprollming.py → uvicorn → health endpoint.
This catches missing entry points, broken imports, and lifecycle regressions.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

PROJECT_DIR = str(Path(__file__).resolve().parents[2])
KRM = str(Path(PROJECT_DIR) / "krm")
PORT = 18777
BASE_URL = f"http://127.0.0.1:{PORT}"
SERVE_PORT = 18778
SERVE_BASE_URL = f"http://127.0.0.1:{SERVE_PORT}"


def _minimal_config() -> str:
    return """
routes:
  base/test:
    is_private: true
    model: "test-model"
    upstream_url: "http://127.0.0.1:19999"
  local/main:
    extends: base/test
    pattern: "local/main"
"""


@pytest.fixture(scope="module")
def running_krm():
    """Create a minimal config, start KRM, yield, then stop."""
    workdir = Path(tempfile.mkdtemp(prefix="krm-daemon-e2e-"))
    config_path = workdir / "config.yaml"
    config_path.write_text(_minimal_config(), encoding="utf-8")

    env = os.environ.copy()
    env["CONFIG_FILE"] = str(config_path)
    env["KRM_PYTHON"] = sys.executable

    subprocess.Popen(
        [KRM, "start", "--port", str(PORT), "--log-path", str(workdir)],
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

    if not ok:
        logs = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(workdir.glob("server*.log"))
        )
        subprocess.run(
            [KRM, "stop", "--port", str(PORT), "--log-path", str(workdir), "--force"],
            cwd=PROJECT_DIR, env=env, capture_output=True,
        )
        shutil.rmtree(workdir, ignore_errors=True)
        pytest.fail(f"Daemon did not start within 10s. Logs:\n{logs[-4000:]}")

    try:
        yield
    finally:
        subprocess.run(
            [KRM, "stop", "--port", str(PORT), "--log-path", str(workdir), "--force"],
            cwd=PROJECT_DIR, env=env, capture_output=True,
        )
        shutil.rmtree(workdir, ignore_errors=True)


def test_krm_health_check(running_krm):
    """KRM-managed server is running and health endpoint responds."""
    resp = httpx.get(f"{BASE_URL}/health", timeout=10)
    assert resp.status_code == 200


def test_krm_models_endpoint(running_krm):
    """Models endpoint returns at least one route."""
    resp = httpx.get(f"{BASE_URL}/v1/models", timeout=10)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_krm_serve_health_check(tmp_path):
    """The foreground KRM command starts the configured server."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_config(), encoding="utf-8")
    env = os.environ.copy()
    env["KRM_PYTHON"] = sys.executable
    process = subprocess.Popen(
        [KRM, "serve", "--port", str(SERVE_PORT), "--config", str(config_path)],
        cwd=PROJECT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if httpx.get(f"{SERVE_BASE_URL}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            if process.poll() is None:
                process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            pytest.fail(f"KRM serve did not start within 10s. stdout={stdout!r} stderr={stderr!r}")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
