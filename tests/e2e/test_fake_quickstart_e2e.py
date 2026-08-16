"""The documented fake quick-start must remain an executable user journey."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_fake_quickstart_serves_its_documented_route() -> None:
    proxy_port = _free_port()
    fake_port = _free_port()
    process = subprocess.Popen(
        [
            "bash",
            "scripts/start-with-fake.sh",
            "--port",
            str(proxy_port),
            "--fake-port",
            str(fake_port),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.communicate(timeout=1)[0]
                raise AssertionError(f"quick-start exited early:\n{output}")
            try:
                response = httpx.post(
                    f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
                    json={
                        "model": "internal/fake",
                        "messages": [{"role": "user", "content": "Hello"}],
                        "stream": False,
                    },
                    timeout=1,
                )
            except httpx.HTTPError:
                time.sleep(0.2)
                continue
            assert response.status_code == 200, response.text
            assert response.json()["choices"][0]["message"]["content"] == "FAKE BACKEND OK"
            return
        raise AssertionError("quick-start did not become ready within 15 seconds")
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
