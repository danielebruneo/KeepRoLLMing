from __future__ import annotations

import os
import socket
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class BackendTarget:
    mode: str
    base_url: str
    client_model_basic: str
    client_model_summary: str | None = None
    summary_model: str | None = None
    control_url: str | None = None


@dataclass
class RunningServer:
    base_url: str
    process: subprocess.Popen[str]
    stdout_path: Path
    stderr_path: Path
    workdir: Path
    perf_dir: Path
    cache_dir: Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _normalize_base_url(url: str) -> str:
    s = url.rstrip("/")
    if s.endswith("/v1"):
        s = s[:-3]
    return s


def _wait_for_http(url: str, *, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code < 500:
                return
        except Exception as err:  # pragma: no cover - retry loop
            last_err = err
        time.sleep(0.1)
    raise RuntimeError(f"service did not come up: {url}; last_err={last_err}")


def _runner_code(module_name: str, attr_name: str, host: str, port: int) -> str:
    return textwrap.dedent(
        f"""
        import importlib
        import uvicorn

        module = importlib.import_module({module_name!r})
        target = getattr(module, {attr_name!r})
        app = target() if callable(target) and {attr_name!r} == 'create_app' else target
        uvicorn.run(app, host={host!r}, port={port}, log_level='warning')
        """
    )


def _spawn_uvicorn(
    app_ref: str,
    *,
    port: int,
    env: Dict[str, str],
    workdir: Path,
    health_url: str,
    perf_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> RunningServer:
    stdout_path = workdir / f"{app_ref.replace(':', '_').replace('.', '_')}.stdout.log"
    stderr_path = workdir / f"{app_ref.replace(':', '_').replace('.', '_')}.stderr.log"
    module_name, attr_name = app_ref.split(":", 1)
    cmd = [sys.executable, "-c", _runner_code(module_name, attr_name, "127.0.0.1", port)]

    with stdout_path.open("w", encoding="utf-8") as stdout_fh, stderr_path.open("w", encoding="utf-8") as stderr_fh:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=env,
            stdout=stdout_fh,
            stderr=stderr_fh,
            text=True,
        )
    try:
        _wait_for_http(health_url)
    except Exception as exc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        stdout_tail = stdout_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeError(
            f"service did not come up: {health_url}; exc={exc}\n--- stdout ---\n{stdout_tail}\n--- stderr ---\n{stderr_tail}"
        ) from exc

    return RunningServer(
        base_url=f"http://127.0.0.1:{port}",
        process=proc,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        workdir=workdir,
        perf_dir=perf_dir or (workdir / "performance_logs"),
        cache_dir=cache_dir or (workdir / "summary_cache"),
    )


def _stop_server(server: RunningServer) -> None:
    server.process.terminate()
    try:
        server.process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        server.process.kill()
        server.process.wait(timeout=5)


@pytest.fixture
def fake_backend_server(tmp_path: Path):
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    server = _spawn_uvicorn(
        "tests.e2e.fake_backend:create_app",
        port=port,
        env=env,
        workdir=ROOT,
        health_url=f"http://127.0.0.1:{port}/__health",
    )
    try:
        httpx.post(f"{server.base_url}/__reset", timeout=5.0)
        yield server
    finally:
        _stop_server(server)


@pytest.fixture
def backend_target(request: pytest.FixtureRequest) -> BackendTarget:
    mode = getattr(request, "param", "fake")
    if mode == "fake":
        fake_backend_server = request.getfixturevalue("fake_backend_server")
        return BackendTarget(
            mode="fake",
            base_url=fake_backend_server.base_url,
            client_model_basic="pass/main-model",
            client_model_summary="local/quick",
            summary_model="summary-model",
            control_url=fake_backend_server.base_url,
        )

    if mode == "live":
        raw_base = os.getenv("E2E_LIVE_BASE_URL")
        live_model = os.getenv("E2E_LIVE_MODEL")

        # Enhanced logging for live backend configuration
        print(f"\n=== E2E LIVE TEST CONFIGURATION ===")
        print(f"UPSTREAM_BASE_URL environment variable: {raw_base or 'NOT SET'}")
        print(f"LIVE_MODEL environment variable: {live_model or 'NOT SET'}")
        print(f"CLIENT_SUMMARY_MODEL environment variable: {os.getenv('E2E_LIVE_CLIENT_SUMMARY_MODEL', 'local/quick')}")
        print(f"SUMMARY_MODEL environment variable: {os.getenv('E2E_LIVE_SUMMARY_MODEL', 'None')}")
        print("==================================\n")

        if not raw_base or not live_model:
            print("SKIPPING LIVE TEST - Required environment variables NOT SET:")
            print(f"  E2E_LIVE_BASE_URL={raw_base or 'NOT SET'}")
            print(f"  E2E_LIVE_MODEL={live_model or 'NOT SET'}")
            pytest.skip("Set E2E_LIVE_BASE_URL and E2E_LIVE_MODEL to run live E2E tests")

        return BackendTarget(
            mode="live",
            base_url=_normalize_base_url(raw_base),
            client_model_basic=f"pass/{live_model}",
            client_model_summary=os.getenv("E2E_LIVE_CLIENT_SUMMARY_MODEL", "local/quick"),
            summary_model=os.getenv("E2E_LIVE_SUMMARY_MODEL"),
            control_url=None,
        )

    raise AssertionError(f"unknown backend mode: {mode}")


@pytest.fixture
def orchestrator_server(tmp_path: Path, backend_target: BackendTarget):
    port = _free_port()
    env = os.environ.copy()
    # Set the test config file
    env["CONFIG_FILE"] = str(ROOT / "tests" / "config.yaml")
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
            "UPSTREAM_BASE_URL": backend_target.base_url,
            "MAIN_MODEL": "main-model",
            "SUMMARY_MODEL": backend_target.summary_model or "summary-model",
            "QUICK_MAIN_MODEL": "main-model",
            "QUICK_SUMMARY_MODEL": backend_target.summary_model or "summary-model",
            "BASE_MAIN_MODEL": "main-model",
            "BASE_SUMMARY_MODEL": backend_target.summary_model or "summary-model",
            "DEEP_MAIN_MODEL": "main-model",
            "DEEP_SUMMARY_MODEL": backend_target.summary_model or "summary-model",
            "PERFORMANCE_LOGS_DIR": str(tmp_path / "performance_logs"),
            "SUMMARY_CACHE_DIR": str(tmp_path / "summary_cache"),
            "LOG_MODE": "DEBUG",
            "DEFAULT_CTX_LEN": "4096",
            "MAX_SUMMARY_BACKEND_ATTEMPTS": "6",
        }
    )
    perf_dir = tmp_path / "performance_logs"
    cache_dir = tmp_path / "summary_cache"
    
    # Ensure that we create a unique cache directory for this specific test
    # This is to ensure proper isolation even when running in parallel mode
    # Also clean the cache directory before using it to prevent any cross-contamination
    if cache_dir.exists():
        import shutil
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    server = _spawn_uvicorn(
        "keeprollming.app:app",
        port=port,
        env=env,
        workdir=ROOT,
        health_url=f"http://127.0.0.1:{port}/docs",
        perf_dir=perf_dir,
        cache_dir=cache_dir,
    )
    try:
        yield server
    finally:
        _stop_server(server)


@pytest.fixture
def backend_client() -> httpx.Client:
    with httpx.Client(timeout=20.0) as client:
        yield client


@pytest.fixture
def configure_fake_backend(backend_target: BackendTarget, backend_client: httpx.Client):
    def _configure(scenario: Dict[str, Any]) -> None:
        if backend_target.mode != "fake" or not backend_target.control_url:
            return
        backend_client.post(f"{backend_target.control_url}/__scenario", json={"scenario": scenario}).raise_for_status()

    return _configure


@pytest.fixture
def get_fake_stats(backend_target: BackendTarget, backend_client: httpx.Client):
    def _get() -> Dict[str, Any]:
        if backend_target.mode != "fake" or not backend_target.control_url:
            return {}
        resp = backend_client.get(f"{backend_target.control_url}/__stats")
        resp.raise_for_status()
        return resp.json()

    return _get
