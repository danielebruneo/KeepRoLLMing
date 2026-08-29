"""Real-HTTP stream-lifetime regression tests.

These tests intentionally use the subprocess KRM server and fake upstream
server.  Direct generator tests cannot prove that a downstream disconnect
actually closes the ``httpx`` response held by the proxy.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
import pytest


async def _wait_for_no_active_upstream_streams(control_url: str, timeout: float = 3.0) -> dict:
    """Wait until the fake backend observes all streaming generators closed."""
    deadline = time.monotonic() + timeout
    last_stats: dict = {}
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() < deadline:
            response = await client.get(f"{control_url}/__stats")
            response.raise_for_status()
            last_stats = response.json()
            if last_stats.get("active_streams") == 0:
                return last_stats
            await asyncio.sleep(0.05)
    raise AssertionError(f"upstream stream remained active after downstream close: {last_stats}")


@pytest.mark.parametrize("backend_target", ["fake"], indirect=True)
class TestStreamLifecycleE2E:
    def test_sigterm_shuts_down_cleanly(self, orchestrator_server) -> None:
        """The subprocess server exits cleanly without relying on a forced kill."""
        process = orchestrator_server.process
        process.terminate()
        exit_code = process.wait(timeout=5.0)

        # Uvicorn may preserve SIGTERM as its subprocess status (-15), but it
        # must still exit within the graceful timeout without a forced kill.
        assert exit_code in {0, -15}
        lifecycle_log = (
            orchestrator_server.perf_dir.parent / "logs" / "keeprollming.log.json"
        )
        assert '"type": "execution.app.stopping"' in lifecycle_log.read_text(
            encoding="utf-8", errors="replace"
        )
        stderr = orchestrator_server.stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )
        assert "Task was destroyed but it is pending" not in stderr

    def test_shared_upstream_pool_times_out_while_a_stream_holds_only_slot(
        self,
        backend_target,
        configure_fake_backend,
    ) -> None:
        """Pool acquisition is bounded independently from a request deadline."""
        assert backend_target.control_url
        configure_fake_backend(
            {
                "chat": {
                    "stream_pieces": ["first", "second"],
                    "chunk_delay_ms": 500,
                },
            }
        )
        script = r"""
import asyncio
import sys
import httpx
from keeprollming.upstream import (
    close_http_client, configure_http_transport, http_client, make_request_timeout,
)

async def main(base_url):
    configure_http_transport({
        "max_connections": 1,
        "max_keepalive_connections": 0,
        "pool_timeout": 0.05,
        "connect_timeout": 2,
    })
    client = await http_client(request_timeout=5)
    payload = {
        "model": "main-model",
        "messages": [{"role": "user", "content": "hold the connection"}],
        "stream": True,
    }
    try:
        async with client.stream(
            "POST", base_url + "/v1/chat/completions", json=payload,
            timeout=make_request_timeout(5),
        ) as held:
            held.raise_for_status()
            try:
                await client.post(
                    base_url + "/v1/chat/completions", json=payload,
                    timeout=make_request_timeout(5),
                )
            except httpx.PoolTimeout:
                return
            raise AssertionError("second request unexpectedly acquired the only pool slot")
    finally:
        await close_http_client()

asyncio.run(main(sys.argv[1]))
"""
        env = os.environ.copy()
        result = subprocess.run(
            [sys.executable, "-c", script, backend_target.base_url],
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.asyncio
    async def test_downstream_disconnect_closes_long_lived_upstream_stream(
        self,
        orchestrator_server,
        backend_target,
        configure_fake_backend,
    ) -> None:
        """Closing a client stream releases the proxy's upstream HTTP response."""
        assert backend_target.control_url
        configure_fake_backend(
            {
                "chat": {
                    # Large pieces fill the local TCP send buffer after the peer
                    # aborts, forcing the ASGI server to observe the disconnect
                    # instead of relying on graceful close timing.
                    "stream_pieces": ["x" * 16384 for _ in range(100)],
                    "chunk_delay_ms": 1,
                },
            }
        )

        parsed = urlsplit(orchestrator_server.base_url)
        body = json.dumps(
            {
                "model": "local/main",
                "messages": [{"role": "user", "content": "start a long stream"}],
                "stream": True,
            }
        ).encode()
        reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
        writer.write(
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {parsed.hostname}:{parsed.port}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2.0)
        assert b"200 OK" in headers
        assert b"cache-control: no-cache" in headers.lower()
        assert b"x-accel-buffering: no" in headers.lower()
        first_bytes = await asyncio.wait_for(reader.read(4096), timeout=2.0)
        assert b"data: " in first_bytes
        # ``abort`` closes the TCP transport immediately, rather than waiting
        # for the peer to finish a response whose body we intentionally stop
        # reading. This is the failure mode KRM must handle.
        writer.transport.abort()

        stats = await _wait_for_no_active_upstream_streams(backend_target.control_url)
        assert stats["streams_started"] == 1
        assert stats["streams_closed"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_streams_finish_without_cross_request_blocking(
        self,
        orchestrator_server,
        backend_target,
        configure_fake_backend,
    ) -> None:
        """Several independent streams complete and release every upstream generator."""
        assert backend_target.control_url
        configure_fake_backend(
            {
                "chat": {
                    "stream_pieces": ["A", "B", "C", "D"],
                    "chunk_delay_ms": 25,
                },
            }
        )

        async def consume_one(index: int) -> str:
            async with httpx.AsyncClient(timeout=5.0) as client:
                async with client.stream(
                    "POST",
                    f"{orchestrator_server.base_url}/v1/chat/completions",
                    json={
                        "model": "local/main",
                        "messages": [{"role": "user", "content": f"stream {index}"}],
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    return b"".join([chunk async for chunk in response.aiter_bytes()]).decode()

        outputs = await asyncio.gather(*(consume_one(index) for index in range(6)))
        assert all("data: [DONE]" in output for output in outputs)

        stats = await _wait_for_no_active_upstream_streams(backend_target.control_url)
        assert stats["streams_started"] == 6
        assert stats["streams_closed"] == 6
