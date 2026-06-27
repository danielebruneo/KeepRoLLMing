"""
StreamingFilterBase — Shared infrastructure for streaming filters.

This module provides a base class for filters that need to buffer chunks,
perform upstream retries, and manage keepalive during streaming.

Designed to reduce code duplication in:
- ToolLoopStopperFilter (TLS)
- ReasoningLoopStopperFilter (RLS)
- ModelNudgeFilter

Usage:
    class MyStreamingFilter(StreamingFilterBase):
        def _should_start_buffering(self, chunk, context):
            # Detect when to start buffering
            return self._has_pattern(chunk)

        def _should_flush_buffer(self, chunk, context):
            # Detect when to flush accumulated buffer
            return self._condition_met(chunk)

        def _handle_intervention(self, context):
            # Implement intervention logic (retry, nudge, etc.)
            return StreamChunkResult(emit=[...])
"""

import asyncio
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...logger import log
from ..filter import Filter, FilterConfig, FilterExecutionContext, RetryDecision, StreamChunkResult


@dataclass
class StreamingFilterConfig(FilterConfig):
    """Base config for streaming filters with retry support."""
    max_retries: int = 2
    retry_timeout: int = 120  # seconds


class StreamingFilterBase(Filter, ABC):
    """
    Base class for streaming filters that need buffering and retry support.

    Handles:
    - Chunk buffering during streaming
    - Retry orchestration with upstream
    - Keepalive chunk management during filter processing
    - Conversation augmentation for retries

    Subclasses implement:
    - _should_start_buffering() — when to start buffering
    - _should_flush_buffer() — when to flush accumulated chunks
    - _handle_intervention() — what to do when intervention is needed

    Example:
        class ToolLoopStopperFilter(StreamingFilterBase):
            priority = 25

            def _should_start_buffering(self, chunk, context):
                return self._has_tool_call_delta(chunk)

            def _should_flush_buffer(self, chunk, context):
                return self._tool_call_complete(chunk)

            async def _handle_intervention(self, context):
                # Check for loop, retry if needed
                if self._is_loop(context):
                    return await self._retry_with_intervention(context)
                return self._flush_buffer(context)
    """

    _default_name: str = "streaming_filter_base"

    def __init__(self, config: Optional[StreamingFilterConfig] = None):
        super().__init__(config or StreamingFilterConfig())
        # Buffer state
        self._stream_buffer: List[bytes] = []
        self._buffering = False
        self._retry_count = 0

        # When True, chunks are forwarded to the client while being buffered.
        # This prevents long silences during nudge lazy-detection (the client
        # sees content live; only the decision to retry happens after the fact).
        self._emit_while_buffering = False

        # Keepalive management
        self._keepalive_task: Optional[asyncio.Task] = None
        self._keepalive_chunks: List[bytes] = []
        self._keepalive_running = False

    # ── Buffer state management ─────────────────────────────────────

    def _start_buffering(self) -> None:
        """Start accumulating chunks."""
        self._buffering = True
        self._stream_buffer = []
        self._retry_count = 0

    def _stop_buffering(self) -> None:
        """Stop buffering and process accumulated chunks."""
        self._buffering = False

    def _should_start_buffering(self, chunk: bytes, context: FilterExecutionContext) -> bool:
        """Override in subclass to detect when to start buffering.

        Args:
            chunk: Raw SSE chunk bytes
            context: Shared execution context

        Returns:
            True if this chunk should trigger buffering
        """
        return False

    def _should_flush_buffer(self, chunk: bytes, context: FilterExecutionContext) -> bool:
        """Override in subclass to detect when to flush buffer.

        Args:
            chunk: Current chunk that may trigger flush
            context: Shared execution context

        Returns:
            True if accumulated buffer should be flushed
        """
        return False

    def _flush_buffer(self, context: FilterExecutionContext) -> StreamChunkResult:
        """Flush accumulated buffer to client.

        Returns:
            StreamChunkResult with all buffered chunks in emit
        """
        result = StreamChunkResult(emit=list(self._stream_buffer))
        self._stream_buffer = []
        return result

    # ── Retry orchestration ─────────────────────────────────────────

    async def _execute_retry(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        upstream_url: str,
        original_payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Execute HTTP retry with conversation augmentation.

        Args:
            messages: Augmented conversation to send
            model: Model name for the request
            upstream_url: Base upstream URL
            original_payload: Original request payload for parameter inheritance

        Returns:
            Upstream response dict or None on failure
        """
        import httpx

        if "/chat/completions" in upstream_url:
            url = upstream_url
        else:
            url = f"{upstream_url}/v1/chat/completions"

        # Build full upstream payload with all parameters from original request
        if original_payload:
            body = dict(original_payload)
            body["model"] = model
            body["messages"] = messages
            body["stream"] = False
            body.pop("_original_model", None)
        else:
            body = {"model": model, "messages": messages, "stream": False}

        headers = {"Content-Type": "application/json"}
        api_key = getattr(self.config, 'api_key', None)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.config.retry_timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            req_id = self._resolve_req_id(context)
            log("ERROR", "streaming_filter_retry_failed",
                req_id=req_id, error=str(e), url=upstream_url)
            return None

    def _augment_conversation(
        self,
        conv: List[Dict[str, Any]],
        intervention: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Add intervention message to conversation for retry.

        Args:
            conv: Original conversation history
            intervention: Message to add (tool result, user message, etc.)

        Returns:
            Augmented conversation list
        """
        augmented = deepcopy(conv)
        augmented.append(intervention)
        return augmented

    def _get_conversation(self, context: FilterExecutionContext) -> List[Dict[str, Any]]:
        """Extract conversation history from context.

        Args:
            context: Shared execution context

        Returns:
            Conversation messages list
        """
        conv = context.metadata.get("conversation_history", []) or []
        if not conv:
            payload = context.upstream_payload or {}
            conv = deepcopy(payload.get("messages", []))
        return conv or []

    # ── Keepalive management ────────────────────────────────────────

    async def _start_keepalive(self) -> None:
        """Start keepalive chunk task during filter processing."""
        self._keepalive_running = True
        self._keepalive_chunks = []

        async def _keepalive_producer():
            while self._keepalive_running:
                await asyncio.sleep(15)
                if self._keepalive_running:
                    self._keepalive_chunks.append(b'data: {"keepalive":true}\n\n')

        self._keepalive_task = asyncio.create_task(_keepalive_producer())

    async def _stop_keepalive(self) -> List[bytes]:
        """Stop keepalive task and return accumulated chunks.

        Returns:
            List of keepalive chunk bytes
        """
        self._keepalive_running = False
        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        chunks = self._keepalive_chunks
        self._keepalive_chunks = []
        return chunks

    # ── Chunk processing — base implementation ──────────────────────

    async def process_stream_chunk(
        self,
        chunk: bytes,
        context: FilterExecutionContext,
    ) -> StreamChunkResult:
        """
        Base implementation: buffer chunks, detect conditions, retry if needed.

        Subclasses can override to customize behavior, but should call super()
        or replicate this logic.

        Args:
            chunk: Raw SSE chunk bytes
            context: Shared execution context

        Returns:
            StreamChunkResult with emit/buffer/retry/stop
        """
        req_id = self._resolve_req_id(context)

        # Check if we should start buffering
        if not self._buffering and self._should_start_buffering(chunk, context):
            self._start_buffering()
            self._stream_buffer.append(chunk)
            log("INFO", "streaming_buffer_start",
                req_id=req_id, filter=self.name)
            # Check if this first chunk already triggers a flush (e.g. content
            # and finish_reason in the same SSE message — common with DeepSeek).
            if self._should_flush_buffer(chunk, context):
                self._stop_buffering()
                return await self._handle_intervention(context)
            if self._emit_while_buffering:
                return StreamChunkResult(emit=[chunk])  # Forward while buffering
            return StreamChunkResult(buffer=None)  # Hold this chunk

        # If buffering, accumulate
        if self._buffering:
            self._stream_buffer.append(chunk)

            # Check if we should flush
            if self._should_flush_buffer(chunk, context):
                self._stop_buffering()
                # Call subclass's intervention handler to check for retry conditions
                return await self._handle_intervention(context)

            if self._emit_while_buffering:
                return StreamChunkResult(emit=[chunk])  # Forward while buffering
            return StreamChunkResult(buffer=None)  # Keep buffering

        # Normal pass-through
        return StreamChunkResult(emit=[chunk])



