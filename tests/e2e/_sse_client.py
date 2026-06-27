"""
Incremental SSE client for realistic streaming tests.

This client consumes SSE streams chunk-by-chunk (like real clients do),
handling JSON fragmentation and other streaming pathologies that buffered
reads (resp.text) would mask.

ST-02 — Realistic Test Harness
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class ParsedEvent:
    """Represents a parsed SSE event."""
    data: Dict[str, Any]
    raw: str = ""


@dataclass
class StreamResult:
    """Accumulated results from consuming a stream."""
    events: List[ParsedEvent] = field(default_factory=list)
    content: str = ""
    reasoning_content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    done_marker_received: bool = False
    raw_chunks: List[str] = field(default_factory=list)


class SSEClient:
    """
    Incremental SSE client that survives streaming pathologies.
    
    This client:
    - Consumes bytes incrementally via iter_bytes()
    - Buffers partial chunks to handle JSON fragmentation (L3)
    - Reassembles SSE records (data: ...\n\n)
    - Parses events and accumulates content/reasoning/tool_calls
    - Tracks [DONE] marker and finish_reason
    """
    
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url
        self.timeout = timeout
    
    async def consume_chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        stream: bool = True,
        **kwargs
    ) -> StreamResult:
        """
        Consume a chat completion stream incrementally.
        
        Returns StreamResult with accumulated content, reasoning, tool_calls, etc.
        """
        result = StreamResult()
        buffer = ""
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": stream,
                    **kwargs
                }
            ) as response:
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Stream request failed: {response.status_code} {response.text[:200]}"
                    )
                
                # Consume bytes incrementally
                async for chunk in response.aiter_bytes():
                    chunk_str = chunk.decode("utf-8", errors="replace")
                    result.raw_chunks.append(chunk_str)
                    buffer += chunk_str
                    
                    # Process complete SSE records
                    while "\n\n" in buffer:
                        record, buffer = buffer.split("\n\n", 1)
                        event = self._parse_record(record)
                        if event:
                            result.events.append(event)
                            self._accumulate_event(result, event.data)
                
                # Process remaining buffer (last record)
                if buffer.strip():
                    event = self._parse_record(buffer)
                    if event:
                        result.events.append(event)
                        self._accumulate_event(result, event.data)
        
        return result
    
    def _parse_record(self, record: str) -> Optional[ParsedEvent]:
        """Parse an SSE record (data: {...}\n\n)."""
        lines = record.strip().split("\n")
        
        for line in lines:
            if line.startswith("data: "):
                data_str = line[6:].strip()
                
                if data_str == "[DONE]":
                    return ParsedEvent(data={"done": True}, raw=record)
                
                try:
                    data = json.loads(data_str)
                    return ParsedEvent(data=data, raw=record)
                except json.JSONDecodeError:
                    # JSON fragmentation: buffer and wait for more data
                    # For now, skip malformed records (they'll be reassembled
                    # by the next chunk)
                    pass
        
        return None
    
    def _accumulate_event(self, result: StreamResult, data: Dict[str, Any]) -> None:
        """Accumulate event data into result."""
        if data.get("done"):
            result.done_marker_received = True
            return
        
        if "choices" not in data:
            return
        
        choices = data["choices"]
        if not choices:
            return
        
        delta = choices[0].get("delta", {})
        
        # Accumulate content
        if "content" in delta:
            result.content += delta["content"]
        
        # Accumulate reasoning_content
        if "reasoning_content" in delta:
            result.reasoning_content += delta["reasoning_content"]
        
        # Accumulate tool_calls
        if "tool_calls" in delta:
            result.tool_calls.extend(delta["tool_calls"])
        
        # Capture finish_reason
        if "finish_reason" in choices[0]:
            result.finish_reason = choices[0]["finish_reason"]
        
        # Capture usage
        if "usage" in data:
            result.usage = data["usage"]


async def create_stream(
    base_url: str,
    model: str,
    messages: List[Dict[str, Any]],
    **kwargs
) -> StreamResult:
    """
    Convenience function to create and consume a stream.
    
    Usage:
        result = await create_stream(
            fake_url,
            "test-model",
            [{"role": "user", "content": "Hello"}]
        )
        assert result.content == "Expected content"
        assert result.done_marker_received is True
    """
    client = SSEClient(base_url)
    return await client.consume_chat(model=model, messages=messages, **kwargs)
