"""
Pipeline — filter chain orchestrator for Architecture V2.

The Pipeline manages ordered execution of filter chains for both
streaming and non-streaming request processing. It replaces the
duplicated filter chain logic in chat_completions.py and
streaming_handlers.py with a single, testable orchestrator.

Usage:
    pipeline = Pipeline([
        SystemPromptFilter(config_sp),
        SummarizationFilter(config_summ),
        UpstreamFilter(config_upstream),
        ModelNudgeFilter(config_nudge),
    ])

    # Non-streaming
    response = await pipeline.run(payload, route, req_id)

    # Streaming
    async for chunk in pipeline.run_stream(payload, route, req_id):
        yield chunk
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

from keeprollming.orchestrator.filter import (
    Filter,
    FilterChain,
    FilterExecutionContext,
    StopFilterChain,
)
from keeprollming.logger import log


class Pipeline:
    """Orchestrates ordered filter execution for request processing."""

    # Known filter names → classes
    _FILTER_MAP: dict[str, type] = {}
    _SORTED_NAMES: list[str] = []

    def __init__(self, filters: list[Filter] | None = None):
        self._filters: list[Filter] = filters or []
        self._last_request_context: FilterExecutionContext | None = None

    @classmethod
    def _init_filter_map(cls) -> None:
        """Lazy-init the filter map and priority-sorted order."""
        if cls._FILTER_MAP:
            return
        from keeprollming.orchestrator.filters import (
            MultimodalValidatorFilter, ModelNudgeFilter,
            ReasoningLoopStopperFilter, SummarizationFilter,
            SystemPromptFilter, TimestampFilter,
            ToolLoopStopperFilter, ToolRewriteFilter,
        )
        cls._FILTER_MAP = {
            'system_prompt': SystemPromptFilter,
            'summarization': SummarizationFilter,
            'tool_rewrite': ToolRewriteFilter,
            'reasoning_loop_stopper': ReasoningLoopStopperFilter,
            'model_tool_loop_stopper': ToolLoopStopperFilter,
            'multimodal_validator': MultimodalValidatorFilter,
            'model_nudge': ModelNudgeFilter,
            'timestamp': TimestampFilter,
        }
        cls._SORTED_NAMES = sorted(
            cls._FILTER_MAP.keys(),
            key=lambda n: cls._FILTER_MAP[n].priority,
        )

    @classmethod
    def from_route_config(cls, route_config: dict[str, Any] | None, api_key: str | None = None) -> "Pipeline | None":
        """Build Pipeline from route filter_chain config.

        Two config formats are supported:

        **Legacy** (with explicit ``order``):
        .. code:: yaml

            filter_chain:
              order: [model_tool_loop_stopper, model_nudge]
              filters:
                model_tool_loop_stopper:
                  enabled: true
                model_nudge:
                  enabled: true

        **Simplified** (flat keys, order by filter priority):
        .. code:: yaml

            filter_chain:
              model_tool_loop_stopper:
                enabled: true
              timestamp:
                enabled: false

        In the simplified format only filters with ``enabled: true`` are
        instantiated.  Filters not mentioned are skipped.

        Args:
            route_config: Filter chain config dict from route YAML
            api_key: Optional Bearer token from the route (inherited by filters)
        """
        if not route_config:
            return None
        cls._init_filter_map()

        # Backward compat — legacy format with explicit 'order' key
        if isinstance(route_config, dict) and 'order' in route_config:
            return cls._from_legacy_config(route_config, api_key=api_key)

        # Simplified — flat filter names → configs, ordered by priority
        return cls._from_simple_config(route_config, api_key=api_key)

    @classmethod
    def _from_legacy_config(cls, route_config: dict, api_key: str | None = None) -> "Pipeline | None":
        """Build from legacy ``{order: […], filters: {…}}`` format."""
        try:
            filters = []
            for name in route_config.get('order', []):
                fcls = cls._FILTER_MAP.get(name)
                if fcls is None:
                    continue
                cfg = route_config.get('filters', {}).get(name, {})
                fcfg = dict(cfg, name=name) if isinstance(cfg, dict) else None
                # Inject route-level api_key into filter config if not already set
                if api_key and fcfg is not None and fcfg.get("api_key") is None:
                    fcfg["api_key"] = api_key
                filters.append(fcls(config=fcfg))
            return cls(filters) if filters else None
        except Exception:
            return None

    @classmethod
    def _from_simple_config(cls, route_config: dict, api_key: str | None = None) -> "Pipeline | None":
        """Build from simplified ``{name: {enabled: true, …}}`` format.

        Also handles the ``{filters: {name: …}}`` wrapper (legacy nesting)
        when no ``order`` key is present.
        """
        try:
            # Unwrap legacy `filters:` container when used without `order:`
            if 'filters' in route_config and isinstance(route_config['filters'], dict):
                route_config = route_config['filters']

            filters = []
            for name in cls._SORTED_NAMES:
                cfg = route_config.get(name)
                if isinstance(cfg, dict) and cfg.get("enabled") is True:
                    fcfg = dict(cfg, name=name)
                    # Inject route-level api_key into filter config if not already set
                    if api_key and fcfg.get("api_key") is None:
                        fcfg["api_key"] = api_key
                    filters.append(cls._FILTER_MAP[name](config=fcfg))
            return cls(filters) if filters else None
        except Exception:
            return None

    @property
    def filters(self) -> list[Filter]:
        """Return filters sorted by priority (lowest first)."""
        return sorted(self._filters, key=lambda f: f.priority)

    def add(self, filter_: Filter) -> "Pipeline":
        """Add a filter to the pipeline. Returns self for chaining."""
        self._filters.append(filter_)
        return self

    # ── Request phase ───────────────────────────────────────────────

    async def process_request(
        self,
        payload: dict[str, Any],
        req_id: str,
        upstream_model: str,
        route_name: str = "",
        upstream_url: str = "",
    ) -> dict[str, Any]:
        """Run all request-phase filters on the payload.

        Returns the (possibly modified) payload.
        """
        context = FilterExecutionContext(
            req_id=req_id,
            upstream_payload=dict(payload),
            route_name=route_name,
            upstream_model=upstream_model,
            upstream_url=upstream_url,
        )

        for f in self.filters:
            if not f.is_enabled:
                continue
            try:
                class _Req:
                    def __init__(self, msgs, mod, stream):
                        self.messages = msgs
                        self.model = mod
                        self.stream = stream

                req = _Req(
                    list(payload.get("messages", [])),
                    upstream_model,
                    payload.get("stream", False),
                )
                req = await f.process_request(req, context)
                payload["messages"] = req.messages
            except Exception as e:
                log("ERROR", "pipeline_process_request_error",
                    req_id=req_id, filter=f.__class__.__name__, error=str(e))

        self._last_request_context = context
        return payload

    # ── Response phase ──────────────────────────────────────────────

    async def process_response(
        self,
        response: Any,
        payload: dict[str, Any],
        req_id: str,
        upstream_model: str,
        route_name: str = "",
        upstream_url: str = "",
        is_streaming: bool = False,
        upstream_caller: Any = None,
        context_metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Run all response-phase filters on the response.

        Filters with supports_streaming=False are skipped in streaming mode.
        Filters can call context._upstream_caller to make upstream retry calls.

        Args:
            context_metadata: Optional metadata dict to inject into the
                FilterExecutionContext (e.g. tls_cleared flag).
        """
        context = FilterExecutionContext(
            req_id=req_id,
            upstream_payload=dict(payload),
            route_name=route_name,
            upstream_model=upstream_model,
            upstream_url=upstream_url,
            is_streaming_post_process=is_streaming,
        )
        if context_metadata:
            context.metadata.update(context_metadata)
        context._upstream_caller = upstream_caller

        for f in self.filters:
            if not f.is_enabled:
                continue
            if is_streaming and not f.supports_streaming:
                continue
            try:
                response = await f.process_response(response, context)
            except StopFilterChain as e:
                log("INFO", f"filter_triggered_{e.action}",
                    req_id=req_id, filter=f.__class__.__name__,
                    action=e.action, message=e.message)
            except Exception as e:
                log("ERROR", "pipeline_process_response_error",
                    req_id=req_id, filter=f.__class__.__name__, error=str(e))

        return response

    # ── Streaming filter chain ───────────────────────────────────────

    async def _process_stream_with_filters(
        self,
        upstream_stream,
        payload: dict[str, Any],
        req_id: str,
        upstream_model: str,
        route_name: str = "",
        upstream_url: str = "",
        upstream_gen: Optional[AsyncIterator[bytes]] = None,
    ) -> AsyncIterator[bytes]:
        """Stream chunks through filter chain. Filters may buffer/emit/retry.
        If upstream_gen is provided, use it directly (keeping the HTTP connection
        alive). Otherwise, call upstream_stream(payload) to create one."""
        from .filter import FilterExecutionContext, StreamChunkResult

        context = FilterExecutionContext(
            req_id=req_id,
            upstream_payload=dict(payload),
            route_name=route_name,
            upstream_model=upstream_model,
            upstream_url=upstream_url,
            is_streaming_post_process=True,
        )
        self._last_request_context = context

        gen = upstream_gen if upstream_gen is not None else upstream_stream(payload)

        chunk_count = 0
        async for chunk in gen:
            chunk_count += 1
            if chunk_count == 1:
                log("INFO", "pipeline_stream_first_chunk",
                    req_id=req_id, size=len(chunk),
                    preview=chunk[:80].decode("utf-8", errors="replace"))
            elif chunk_count % 500 == 0:
                log("DEBUG", "pipeline_stream_chunk_progress",
                    req_id=req_id, count=chunk_count)
            should_yield = True
            for f in self.filters:
                if not f.is_enabled or not f.supports_streaming:
                    continue
                try:
                    result = await f.process_stream_chunk(chunk, context)
                except Exception as e:
                    log("ERROR", "pipeline_stream_filter_error",
                        req_id=req_id, filter=f.__class__.__name__, error=str(e))
                    continue
                if result.emit:
                    if should_yield:
                        for emit_chunk in result.emit:
                            # Never yield finish_reason or [DONE] during streaming —
                            # Phase 4 handles the final finish_reason + [DONE].
                            if _has_non_null_finish_reason(emit_chunk) or emit_chunk.strip() == b'data: [DONE]':
                                continue
                            yield emit_chunk
                        should_yield = False
                elif result.buffer is not None:
                    # Flush buffer (result.buffer is a list of buffered chunks)
                    for buf_chunk in result.buffer:
                        yield buf_chunk
                    should_yield = False
                elif result.retry:
                    log("INFO", "pipeline_stream_retry",
                        req_id=req_id, filter=f.__class__.__name__,
                        chunks_processed=chunk_count)
                    context.metadata["_streaming_retry_decision"] = result.retry
                    return
                elif result.stop:
                    log("INFO", "pipeline_stream_stop",
                        req_id=req_id, filter=f.__class__.__name__,
                        chunks_processed=chunk_count)
                    return
                else:
                    # Filter explicitly consumed the chunk:
                    # - buffer=None (start/continue buffering), or
                    # - no emit (filter is holding/processing the chunk)
                    should_yield = False
            if should_yield:
                # Never yield finish_reason, or [DONE] during streaming —
                # Phase 4 handles the final finish_reason + [DONE].  Tool_call
                # fragments are yielded raw (Approccio C — client li mergea).
                if not _has_non_null_finish_reason(chunk) and chunk.strip() != b'data: [DONE]':
                    yield chunk
        # Upstream stream exhausted normally (no retry, no stop)
        log("DEBUG", "pipeline_stream_exhausted",
            req_id=req_id, total_chunks=chunk_count)
        # If a filter still has an active buffer (never flushed because the
        # upstream stream ended without finish_reason), save it for Phase 4.
        # Otherwise the buffered content is silently lost.
        for f in self.filters:
            buf = getattr(f, '_stream_buffer', None)
            if buf and getattr(f, '_buffering', False) and len(buf) > 0:
                context.metadata.setdefault("_buffer_chunks", []).extend(list(buf))
                context.metadata["_buffer_re_streamed"] = getattr(f, '_emit_while_buffering', False)
                log("DEBUG", "pipeline_buffer_orphaned",
                    req_id=req_id, filter=f.__class__.__name__,
                    chunks=len(buf))

    # ── Full run (streaming) ─────────────────────────────────────────

    async def run_stream(
        self,
        payload: dict[str, Any],
        req_id: str,
        upstream_model: str,
        route_name: str = "",
        upstream_url: str = "",
        upstream_stream=None,
    ) -> AsyncIterator[bytes]:
        """Run the full pipeline for streaming requests.

        Request filters → yield chunks from upstream → response filters → yield remaining.

        The caller provides upstream_stream, an async callable(payload) that yields
        raw chunks (bytes). Pipeline wraps it with request/response filter phases.

        Args:
            payload: Request payload
            req_id: Request ID
            upstream_model: Resolved upstream model
            route_name: Route name
            upstream_url: Upstream base URL
            upstream_stream: Async callable(payload) → AsyncIterator[bytes]

        Yields:
            Processed streaming chunks (bytes), including final [DONE]
        """
        log("INFO", "pipeline_run_stream_entry",
            req_id=req_id, has_upstream=upstream_stream is not None)

        # Phase 1: Request filters
        payload = await self.process_request(
            payload, req_id, upstream_model, route_name, upstream_url
        )

        log("INFO", "pipeline_phase1_done",
            req_id=req_id, has_upstream=upstream_stream is not None)

        # Phase 2: Stream chunks from upstream through filter chain
        captured_chunks: list[bytes] = []
        _upstream_ref = upstream_stream(payload) if upstream_stream else None
        if _upstream_ref:
            log("INFO", "pipeline_phase2_start",
                req_id=req_id)
            async for chunk in self._process_stream_with_filters(
                upstream_stream, payload, req_id, upstream_model,
                route_name, upstream_url,
                upstream_gen=_upstream_ref,
            ):
                captured_chunks.append(chunk)
                yield chunk
            # If Phase 2 ended via retry the upstream generator may still
            # be alive (not fully consumed), holding the HTTP connection
            # open until GC — which can take ~10s (upstream keep-alive).
            # Close it immediately so the next Phase (3/4) isn't delayed.
            await _upstream_ref.aclose()
            log("INFO", "pipeline_phase2_end",
                req_id=req_id, captured_chunks=len(captured_chunks))
        else:
            log("WARN", "pipeline_no_upstream",
                req_id=req_id)

        # Phase 3: Accumulate retry content and process through filter chain (V1 mode)
        from .filter import StreamingResponse as _SR
        all_retry_content = ""
        all_retry_tool_calls = []
        retry_finish_reason = None

        # Check for retry_decision from streaming
        if self._last_request_context:
            retry_decision = self._last_request_context.metadata.get("_streaming_retry_decision")
            if retry_decision and upstream_stream:
                log("INFO", "pipeline_phase3_retry_start",
                    req_id=req_id, max_retries=retry_decision.max_retries,
                    filter=retry_decision.intervention_message[:50] if retry_decision.intervention_message else "")
                for retry_attempt in range(retry_decision.max_retries + 1):
                    rp = dict(payload)
                    rp["messages"] = retry_decision.messages
                    rp["stream"] = True
                    rc = []
                    async for ch in upstream_stream(rp):
                        rc.append(ch)
                    rtext = b"".join(rc).decode("utf-8")
                    rcont = ""
                    rtc = []
                    for line in rtext.split("\n"):
                        if line.startswith("data: ") and "[DONE]" not in line:
                            try:
                                d = json.loads(line[6:].strip())
                                for c in d.get("choices", []):
                                    dl = c.get("delta", {})
                                    if "content" in dl: rcont += dl["content"]
                                    if "tool_calls" in dl: rtc.extend(dl["tool_calls"])
                                    if dl.get("finish_reason"):
                                        retry_finish_reason = dl["finish_reason"]
                            except Exception:
                                pass
                    log("DEBUG", "pipeline_phase3_retry_result",
                        req_id=req_id, attempt=retry_attempt,
                        has_content=bool(rcont), tool_calls_count=len(rtc),
                        finish_reason=retry_finish_reason)
                    if rcont or rtc:
                        if all_retry_content: all_retry_content += "\n" + rcont
                        else: all_retry_content = rcont
                        all_retry_tool_calls.extend(rtc)
                        if not all_retry_content.rstrip().endswith(":") if rcont else rtc: break
                        if rtc: break
                log("DEBUG", "pipeline_phase3_retry_end",
                    req_id=req_id,
                    merged_content_len=len(all_retry_content),
                    merged_tc_count=len(all_retry_tool_calls),
                    finish_reason=retry_finish_reason)

        # (No process_response here to avoid V1 retry issues with upstream URL)

        # Phase 4: Post-stream response processing.
        # Guarantees: finish_reason + [DONE] always reach the client.
        from .filter import StreamingResponse

        finish_reason_yielded = False
        try:
            # If the streaming nudge filter stored buffer chunks in context
            # (instead of emitting them in Phase 2), merge them into captured_chunks
            # so _extract_assistant_text can find content, tool_calls, finish_reason.
            _buf_ctx = self._last_request_context
            if _buf_ctx and _buf_ctx.metadata.get("_buffer_chunks"):
                extra = _buf_ctx.metadata["_buffer_chunks"]
                # Prepend — role chunk (chunks[0]) already captured, add content chunks
                for bc in extra:
                    if bc not in captured_chunks:
                        captured_chunks.append(bc)
            reconstructed_text, tool_calls, finish_reason = _extract_assistant_text(captured_chunks)
            # Strip any stale timestamp from reconstructed_text before merging
            # with pc (which has a fresh timestamp).  Build regex from the
            # TimestampFilter's template (dynamic, user-configurable).
            _ts_regex = _build_timestamp_regex(self._filters)
            if _ts_regex and reconstructed_text:
                stripped = _ts_regex.sub("", reconstructed_text).rstrip()
                if stripped:
                    reconstructed_text = stripped

            # Build merge_content so ModelNudge doesn't re-detect lazy in Phase 4
            merge_content = reconstructed_text
            if all_retry_content:
                merge_content += "\n" + all_retry_content
            merge_tc = list(tool_calls or [])

            # Build context_metadata for process_response so filters (e.g. TLS)
            # can inspect retry tool_calls without them being in mock_response
            # (which would trigger duplicate nudge retry on lazy+tc content).
            phase4_context = {}
            if all_retry_tool_calls:
                phase4_context["_retry_tool_calls"] = list(all_retry_tool_calls)

            mock_response = StreamingResponse(
                content=merge_content,
                model=upstream_model,
                finish_reason=retry_finish_reason or finish_reason,
                tool_calls=merge_tc if merge_tc else None,
            )

            processed = await self.process_response(
                mock_response, payload, req_id, upstream_model,
                route_name, upstream_url, is_streaming=True,
                upstream_caller=upstream_stream,
                context_metadata=phase4_context,
            )

            # Phase 4 yield order: content → tool_calls+fr → [DONE]
            # NEVER yield content or tool_calls after finish_reason.

            # 1. Content first (timestamp footer, retry text, filter modifications)
            # NOTE: "retry" here means the nudge asked the model to continue the
            # same response.  The ORIGINAL lazy content is NOT discarded — it is
            # preserved in `reconstructed_text` and merged with the continuation.
            # This is NOT a "try again from scratch" retry — it's a continuation.
            pc = processed.content if processed and hasattr(processed, 'content') else ""
            if pc and pc.startswith(reconstructed_text.rstrip("\n")):
                # pc already includes the original content (no nudge retry)
                text_to_yield = pc
            elif pc and all_retry_content and pc.startswith(all_retry_content.rstrip("\n")):
                # Phase 3 retry: recontructed_text = original, pc = retry + timestamp
                text_to_yield = reconstructed_text.rstrip() + "\n" + pc
            elif pc and not pc.startswith(reconstructed_text.rstrip("\n")):
                # Phase 4 retry or pc is different from original
                text_to_yield = reconstructed_text.rstrip() + "\n" + pc
            else:
                text_to_yield = reconstructed_text

            if text_to_yield:
                content_to_yield = text_to_yield
                # Strip content already sent in Phase 2 (only when buffer was re-streamed)
                _buf_chunks = _buf_ctx and _buf_ctx.metadata.get("_buffer_chunks")
                _buf_re_streamed = _buf_ctx and _buf_ctx.metadata.get("_buffer_re_streamed", False)
                if not _buf_chunks or (_buf_chunks and _buf_re_streamed):
                    if content_to_yield.startswith(reconstructed_text):
                        content_to_yield = content_to_yield[len(reconstructed_text):]
                if content_to_yield:
                    log("DEBUG", "pipeline_phase4_yield_content",
                        req_id=req_id, content_len=len(content_to_yield),
                        content_preview=content_to_yield[:80])
                    modified_chunk = json.dumps({
                        "choices": [{"delta": {"content": content_to_yield}, "index": 0}]
                    })
                    yield f"data: {modified_chunk}\n\n".encode("utf-8")

            # 2. Yield tool_calls:
            #    a. From processed response (filter chain may have modified/blocked them)
            #    b. From Phase 3 retry (unless suppressed by e.g. TLS loop detection)
            all_tc = []
            if processed and hasattr(processed, 'tool_calls') and processed.tool_calls:
                valid_tc = [tc for tc in processed.tool_calls
                            if isinstance(tc, dict) and tc.get("function", {})]
                all_tc.extend(valid_tc)
            if all_retry_tool_calls and not getattr(processed, '_retry_tc_suppressed', False):
                # Phase 3 retry tool_calls — yield only if a filter (e.g. TLS)
                # didn't detect a loop and set _retry_tc_suppressed on the response.
                for tc in all_retry_tool_calls:
                    if tc not in all_tc:
                        all_tc.append(tc)

            # Filter out tool_calls that already have a tool_result in the
            # conversation (echoed from history).  Only yield truly new
            # tool_calls — already filtered by nudge during buffering.
            # ALSO skip when upstream already sent finish_reason:"tool_calls"
            # in Phase 2 (Approccio C — frammenti raw + fr pass-through).
            if all_tc and _has_upstream_fr_tool_calls(captured_chunks):
                log("DEBUG", "pipeline_phase4_skip_tc_upstream_sent",
                    req_id=req_id, tc_count=len(all_tc))
            elif all_tc:
                log("DEBUG", "pipeline_phase4_yield_tc",
                    req_id=req_id, tc_count=len(all_tc),
                    tc_preview=str({
                        str(tc.get("function", {}))
                        for tc in all_tc
                    })[:120])
                tc_data = {"index": 0, "delta": {"tool_calls": all_tc}}
                tc_chunk = json.dumps({"choices": [tc_data]})
                yield f"data: {tc_chunk}\n\n".encode("utf-8")
                # Finish_reason in a SEPARATE chunk with empty delta
                # (OpenAI standard — LibreChat and other clients reject
                #  finish_reason inline in the tool_calls chunk).
                fr_data = {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                fr_chunk = json.dumps({"choices": [fr_data]})
                yield f"data: {fr_chunk}\n\n".encode("utf-8")
                finish_reason_yielded = True
        except Exception as e:
            log("ERROR", "pipeline_phase4_error", req_id=req_id, error=str(e))
        # 3. Default finish_reason if not yielded by tool_calls path
        if not finish_reason_yielded:
            log("INFO", "pipeline_phase4_fr_stop",
                req_id=req_id, finish_reason="stop")
            stop_chunk = json.dumps({
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            })
            yield f"data: {stop_chunk}\n\n".encode("utf-8")
        # 4. [DONE] always last
        yield b"data: [DONE]\n\n"

    # ── Full run (non-streaming) ─────────────────────────────────────

    async def run(
        self,
        payload: dict[str, Any],
        req_id: str,
        upstream_model: str,
        route_name: str = "",
        upstream_url: str = "",
        upstream_call=None,
        max_retries: int = 3,
    ) -> Any:
        """Run the full pipeline with upstream retry support.

        Request filters → upstream call → response filters → (retry if needed)

        Filters can signal a retry by setting:
            context.metadata["retry_upstream"] = True
            context.metadata["retry_messages"] = [...]  # optional modified messages

        Args:
            payload: Original request payload
            req_id: Request ID
            upstream_model: Resolved upstream model
            route_name: Route name
            upstream_url: Upstream base URL
            upstream_call: Async callable(payload) → response
            max_retries: Maximum upstream retry attempts

        Returns:
            Processed response object
        """
        # Phase 1: Request filters (run once)
        payload = await self.process_request(
            payload, req_id, upstream_model, route_name, upstream_url
        )

        # Phase 2+3: Upstream call + response filters (with retry loop)
        response = None
        for attempt in range(max_retries + 1):
            if upstream_call:
                response = await upstream_call(payload)

            if not response:
                break

            response = await self.process_response(
                response, payload, req_id, upstream_model,
                route_name, upstream_url, is_streaming=False,
                upstream_caller=upstream_call,
            )

            # Check if a filter requested retry
            # (Filters set this via the context passed to process_response)
            if not hasattr(response, '_retry_needed') or not response._retry_needed:
                break

            # Use modified messages for retry if provided
            if hasattr(response, '_retry_messages'):
                payload = dict(payload)
                payload["messages"] = response._retry_messages

        return response


# ── Module-level helpers ───────────────────────────────────────────────


def _has_non_null_finish_reason(raw: bytes) -> bool:
    """Check if a raw SSE chunk contains a non-null finish_reason.

    Phase 2 strips ``finish_reason:"stop"`` (Phase 4 will synthesise it),
    but ALLOWS ``finish_reason:"tool_calls"`` to pass through so the client
    receives the completion signal inline with the raw tool_call fragments.

    The streaming nudge buffer still flushes correctly because
    ``_should_flush_buffer`` in ``model_nudge_filter.py`` checks
    ``choices[0].get("finish_reason")`` (choice level, not delta).
    """
    try:
        txt = raw.decode("utf-8", errors="replace")
        if "[DONE]" in txt:
            return False
        if "data: " not in txt:
            return False
        payload_str = txt.split("data: ", 1)[1].strip()
        if not payload_str:
            return False
        import json as _json
        data = _json.loads(payload_str)
        for choice in data.get("choices", []):
            fr = choice.get("finish_reason")
            if fr is not None:
                # Allow "tool_calls" — the client needs it to know
                # tool_call fragments are complete.
                if fr == "tool_calls":
                    return False
                return True
    except Exception:
        pass
    return False


def _has_upstream_fr_tool_calls(chunks: list[bytes]) -> bool:
    """Check if any captured chunk contains ``finish_reason:"tool_calls"``
    — meaning the upstream already sent it in Phase 2 and Phase 4 should
    NOT duplicate it.
    """
    for ch in chunks:
        try:
            txt = ch.decode("utf-8", errors="replace")
            for line in txt.split("\n"):
                s = line.strip()
                if s.startswith("data: ") and s != "data: [DONE]":
                    payload = s[6:].strip()
                    if payload:
                        import json as _json
                        obj = _json.loads(payload)
                        for choice in obj.get("choices", []):
                            if choice.get("finish_reason") == "tool_calls":
                                return True
        except Exception:
            pass
    return False


def _build_timestamp_regex(filters: list) -> re.Pattern | None:
    """Build a regex from the TimestampFilter's ``_static_prefix``.

    The regex matches the template's static prefix across line breaks
    (flexible ``\\n*``) followed by a timestamp value (non-newline chars).
    This is the same logic used by ``_content_has_timestamp_footer``.
    """
    for f in filters:
        prefix = getattr(f, '_static_prefix', None)
        if prefix:
            import re as _re
            lines = prefix.split('\n')
            escaped = [_re.escape(part) for part in lines]
            return _re.compile(r'\n*'.join(escaped) + r'[^\n]*')
    return None


def _extract_assistant_text(chunks: list[bytes]) -> tuple[str, list[dict], str | None]:
    """Extract accumulated assistant text, tool_calls, and finish_reason from SSE chunks.

    Uses the same parsing logic as `_extract_content_from_buffer` in
    `model_nudge_filter.py` to ensure consistent extraction from both
    regular Phase-2 chunks and nudge buffer chunks.

    Returns:
        (reconstructed_text, tool_calls_list, finish_reason)
    """
    import json as _json
    parts: list[str] = []
    tc_by_index: dict[int, dict] = {}
    final_finish_reason: str | None = None
    for chunk in chunks:
        try:
            for line in chunk.decode("utf-8", errors="replace").split("\n"):
                line_s = line.strip()
                if line_s.startswith("data: ") and line_s != "data: [DONE]":
                    payload = line_s[6:].strip()
                    if payload:
                        try:
                            obj = _json.loads(payload)
                            for choice in obj.get("choices", []):
                                delta = choice.get("delta", {})
                                if isinstance(delta.get("content"), str):
                                    parts.append(delta["content"])
                                tc_list = delta.get("tool_calls")
                                if isinstance(tc_list, list):
                                    for tc in tc_list:
                                        if isinstance(tc, dict):
                                            idx = tc.get("index", 0)
                                            merged = tc_by_index.get(idx, {}).copy()
                                            # id, type: keep first seen
                                            if "id" not in merged and "id" in tc:
                                                merged["id"] = tc["id"]
                                            if "type" not in merged and "type" in tc:
                                                merged["type"] = tc["type"]
                                            # function: deep-merge (delta arguments)
                                            if "function" in tc and isinstance(tc["function"], dict):
                                                fn = merged.get("function", {})
                                                if not isinstance(fn, dict):
                                                    fn = {}
                                                if "name" not in fn and "name" in tc["function"]:
                                                    fn["name"] = tc["function"]["name"]
                                                if "arguments" in tc["function"]:
                                                    fn["arguments"] = fn.get("arguments", "") + tc["function"]["arguments"]
                                                merged["function"] = fn
                                            tc_by_index[idx] = merged
                                fr = choice.get("finish_reason")
                                if fr:
                                    final_finish_reason = fr
                        except _json.JSONDecodeError:
                            pass
        except Exception:
            pass
    all_tool_calls = list(tc_by_index.values())
    return "".join(parts), all_tool_calls, final_finish_reason
