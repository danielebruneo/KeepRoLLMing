"""Confined child processes only: never signal a running development server."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

KRM = Path(__file__).resolve().parents[2] / "krm"


@pytest.fixture
def child(tmp_path):
    script = tmp_path / "keeprollming.py"
    script.write_text(
        "import signal,sys,time\nfrom pathlib import Path\n"
        "def stop(*args):\n"
        " if sys.argv[-1]=='ignore': return\n"
        " time.sleep(float(sys.argv[-1])); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "Path(__file__).with_suffix('.ready').touch()\n"
        "while True: time.sleep(.05)\n"
    )
    processes = []

    def launch(mode):
        script.with_suffix(".ready").unlink(missing_ok=True)
        process = subprocess.Popen([sys.executable, str(script), "--port", "19971", mode])
        processes.append(process)
        deadline = time.monotonic() + 5
        while not script.with_suffix(".ready").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        (tmp_path / "keeprollming_19971.pid").write_text(str(process.pid))
        return process

    yield launch
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def invoke(tmp_path, action="stop", *args, env=None):
    return subprocess.run(
        [str(KRM), action, "--port", "19971", "--log-path", str(tmp_path), *args],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


@pytest.mark.parametrize("action", ["stop", "restart"])
def test_timeout_never_kills_and_restart_never_starts_second_process(tmp_path, child, action):
    process = child("ignore")
    result = invoke(tmp_path, action, "--stop-timeout", "1")
    assert result.returncode != 0
    assert process.poll() is None
    assert (tmp_path / "keeprollming_19971.pid").exists()
    assert "not force-killing" in result.stderr
    assert "Starting keeprollming" not in result.stderr


@pytest.mark.parametrize("timeout", ["0", "4"])
def test_graceful_child_exit_is_awaited_and_pid_cleaned(tmp_path, child, timeout):
    process = child("1.2")
    result = invoke(tmp_path, "stop", "--stop-timeout", timeout)
    assert result.returncode == 0, result.stderr
    assert process.wait(timeout=2) == 0
    assert not (tmp_path / "keeprollming_19971.pid").exists()


def test_exit_during_final_wait_is_not_reported_as_timeout(tmp_path, child):
    process = child("0.2")
    result = invoke(tmp_path, "stop", "--stop-timeout", "01")
    assert result.returncode == 0, result.stderr
    assert process.wait(timeout=2) == 0


def test_force_is_explicit_and_targets_only_verified_child(tmp_path, child):
    process = child("ignore")
    result = invoke(tmp_path, "stop", "--force")
    assert result.returncode == 0, result.stderr
    assert process.wait(timeout=2) == -9


def test_environment_shutdown_budget_is_honored(tmp_path, child):
    process = child("ignore")
    result = invoke(tmp_path, env={**os.environ, "KRM_STOP_TIMEOUT": "1"})
    assert result.returncode != 0 and process.poll() is None


@pytest.mark.parametrize("value", ["-1", "no", "0.5"])
def test_invalid_shutdown_budget_rejected(tmp_path, value):
    assert invoke(tmp_path, "stop", "--stop-timeout", value).returncode == 2


def test_stale_pid_pointing_to_unrelated_process_is_not_signaled(tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        (tmp_path / "keeprollming_19971.pid").write_text(str(unrelated.pid))
        result = invoke(tmp_path, "stop", "--force")
        assert result.returncode != 0 and unrelated.poll() is None
        assert "unverified PID" in result.stderr
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)
