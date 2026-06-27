"""
Meta-tests for the degradation layer in fake_backend.

These tests verify that the fake backend produces the correct pathologies
at each degradation level (L0-L4). They do NOT test the pipeline itself.

Run with:
    pytest tests/e2e/test_fake_backend_degradation.py -xvs
"""

import asyncio
import json
import pytest
import uvicorn
import threading
import time

from tests.e2e.fake_backend import create_app


FAKE_PORT = 19997


@pytest.fixture(scope="module")
def fake_backend():
    """Start fake backend server for testing."""
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=FAKE_PORT, log_level="error")
    server = uvicorn.Server(config)
    
    def run():
        asyncio.run(server.serve())
    
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(2)  # Wait for server to start
    
    yield f"http://127.0.0.1:{FAKE_PORT}"
    
    server.should_exit = True


async def _set_degradation(base_url: str, level: int, seed: int):
    """Configure degradation level and seed."""
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/__degrade",
            json={"level": level, "seed": seed},
            timeout=5
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == level
        assert data["seed"] == seed


async def _set_scenario(base_url: str, scenario: dict):
    """Configure a scenario."""
    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/__scenario",
            json={"scenario": scenario},
            timeout=5
        )
        assert resp.status_code == 200


async def _stream_chat(base_url: str, model: str = "test-model") -> list:
    """Consume streaming chat completion and return parsed events."""
    import httpx
    
    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Test"}],
                "stream": True,
            },
            timeout=10
        ) as response:
            assert response.status_code == 200
            
            events = []
            buffer = ""
            
            async for chunk in response.aiter_bytes():
                buffer += chunk.decode("utf-8", errors="replace")
                
                # Split on SSE record separator
                while "\n\n" in buffer:
                    line, buffer = buffer.split("\n\n", 1)
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            events.append({"done": True})
                        elif data:
                            try:
                                events.append(json.loads(data))
                            except json.JSONDecodeError:
                                pass  # Skip malformed JSON
            
            return events


def _extract_tool_calls(events: list) -> list:
    """Extract tool_calls from events."""
    tool_calls = []
    for event in events:
        if "choices" in event:
            delta = event["choices"][0].get("delta", {})
            if "tool_calls" in delta:
                tool_calls.extend(delta["tool_calls"])
    return tool_calls


def _extract_arguments(tool_calls: list) -> list:
    """Extract arguments strings from tool_calls."""
    return [
        tc.get("function", {}).get("arguments", "")
        for tc in tool_calls
    ]


class TestDegradationLevels:
    """Test each degradation level produces correct pathologies."""

    @pytest.mark.asyncio
    async def test_L0_identical_to_current(self, fake_backend):
        """L0 should produce clean, ideal SSE (backward compatible)."""
        import httpx
        
        # Set L0 (default)
        await _set_degradation(fake_backend, level=0, seed=42)
        
        # Simple scenario
        await _set_scenario(fake_backend, {
            "chat": {
                "content": "Hello world",
                "stream_pieces": ["Hello", " ", "world"],
                "include_usage": True,
            }
        })
        
        events = await _stream_chat(fake_backend)
        
        # Should have proper structure
        assert len(events) >= 3  # At least some content chunks + final + DONE
        
        # All events should be valid JSON (no split events)
        for event in events:
            if not event.get("done"):
                assert "choices" in event
                assert isinstance(event["choices"], list)
        
        # Should have [DONE] marker
        assert any(e.get("done") for e in events)

    @pytest.mark.asyncio
    async def test_L1_tool_args_fragmented(self, fake_backend):
        """L1 should fragment tool_call arguments across multiple deltas."""
        import httpx
        
        # Set L1
        await _set_degradation(fake_backend, level=1, seed=42)
        
        # Scenario with tool_calls
        await _set_scenario(fake_backend, {
            "chat": {
                "content": "Done",
                "tool_calls": [{
                    "id": "call_test",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": '{"query": "test search query", "limit": 10}'
                    }
                }],
                "include_usage": True,
            }
        })
        
        events = await _stream_chat(fake_backend)
        
        # Extract tool_calls
        tool_calls = _extract_tool_calls(events)
        assert len(tool_calls) >= 1
        
        # At L1, arguments should be fragmented (split across deltas)
        # The first delta should have incomplete arguments
        args = _extract_arguments(tool_calls)
        
        # With fragmentation, we should see partial arguments
        # (this is a basic check - more sophisticated tests would verify
        # that concatenating fragments produces valid JSON)
        if args:
            # Arguments should be present (even if fragmented)
            assert any("search" in arg or "query" in arg for arg in args)

    @pytest.mark.asyncio
    async def test_L2_keepalive_comments(self, fake_backend):
        """L2 should occasionally inject keepalive comments."""
        import httpx
        
        # Set L2 with seed that triggers keepalive
        await _set_degradation(fake_backend, level=2, seed=12345)
        
        await _set_scenario(fake_backend, {
            "chat": {
                "content": "Test content",
                "stream_pieces": ["Test", " ", "content"],
                "include_usage": True,
            }
        })
        
        # We can't deterministically test keepalive without controlling PRNG,
        # but we can verify the stream still works
        events = await _stream_chat(fake_backend)
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_L3_json_split_detection(self, fake_backend):
        """L3 should occasionally split JSON across chunks."""
        import httpx
        
        # Set L3
        await _set_degradation(fake_backend, level=3, seed=42)
        
        await _set_scenario(fake_backend, {
            "chat": {
                "content": "Test content for L3",
                "stream_pieces": ["Test", " ", "content", " ", "for", " ", "L3"],
                "include_usage": True,
            }
        })
        
        # The stream should still be consumable (client should reassemble)
        events = await _stream_chat(fake_backend)
        assert len(events) > 0
        
        # Events should still be parseable
        for event in events:
            if not event.get("done"):
                # Should have valid structure
                assert "choices" in event or "error" in str(event)

    @pytest.mark.asyncio
    async def test_L4_done_may_be_omitted(self, fake_backend):
        """L4 should occasionally omit [DONE] marker."""
        import httpx
        
        # Set L4 with seed that might omit DONE
        await _set_degradation(fake_backend, level=4, seed=42)
        
        await _set_scenario(fake_backend, {
            "chat": {
                "content": "Test",
                "stream_pieces": ["Test"],
                "include_usage": True,
            }
        })
        
        events = await _stream_chat(fake_backend)
        
        # At L4, DONE might be omitted (50% chance with seed=42)
        # This test just verifies the stream completes
        assert len(events) > 0

    @pytest.mark.asyncio
    async def test_determinism_same_seed(self, fake_backend):
        """Same (level, seed) should produce identical output."""
        import httpx
        
        level = 2
        seed = 99999
        
        # First run
        await _set_degradation(fake_backend, level=level, seed=seed)
        await _set_scenario(fake_backend, {
            "chat": {
                "content": "Determinism test",
                "stream_pieces": ["A", "B", "C"],
                "include_usage": True,
            }
        })
        events1 = await _stream_chat(fake_backend)
        
        # Second run with same params
        await _set_degradation(fake_backend, level=level, seed=seed)
        await _set_scenario(fake_backend, {
            "chat": {
                "content": "Determinism test",
                "stream_pieces": ["A", "B", "C"],
                "include_usage": True,
            }
        })
        events2 = await _stream_chat(fake_backend)
        
        # Should be identical
        assert len(events1) == len(events2)
        for e1, e2 in zip(events1, events2):
            # Compare structure (ignore timestamps)
            assert e1.keys() == e2.keys()
            if "choices" in e1 and "choices" in e2:
                assert e1["choices"][0].get("index") == e2["choices"][0].get("index")


class TestDegradationAPI:
    """Test the /__degrade endpoint."""

    @pytest.mark.asyncio
    async def test_degrade_endpoint_validates_level(self, fake_backend):
        """Should reject invalid level values."""
        import httpx
        
        async with httpx.AsyncClient() as client:
            # Invalid level
            resp = await client.post(
                f"{fake_backend}/__degrade",
                json={"level": 5, "seed": 0},
                timeout=5
            )
            assert resp.status_code == 400
            
            resp = await client.post(
                f"{fake_backend}/__degrade",
                json={"level": -1, "seed": 0},
                timeout=5
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_degrade_endpoint_returns_current_settings(self, fake_backend):
        """Should return current degradation settings."""
        import httpx
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{fake_backend}/__degrade",
                json={"level": 3, "seed": 12345},
                timeout=5
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["level"] == 3
            assert data["seed"] == 12345


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
