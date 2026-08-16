"""
Pipeline — filter chain orchestrator for canonical architecture.

The Pipeline manages ordered execution of filter chains for both
streaming and non-streaming request processing. It replaces the
duplicated filter chain logic in chat_completions.py and
streaming_handlers.py with a single, testable orchestrator.

Usage:
    pipeline = Pipeline([
        SystemPromptFilter(config_sp),
        SummarizationFilter(config_summ),
        ModelNudgeFilter(config_nudge),
    ])

    # Non-streaming
    response = await pipeline.run(payload, route, req_id)

    # Streaming
    async for chunk in pipeline.run_stream(payload, route, req_id):
        yield chunk
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, AsyncIterator, Mapping

from keeprollming.orchestrator.filter import (
    Filter,
    FilterExecutionContext,
    StopFilterChain,
)
from keeprollming.filters import (
    built_in_filter_modules,
    normalize_filters,
    validate_filter_module_settings,
)
from keeprollming.observability import events_pipeline as _pipe
from keeprollming.streaming.accounting import ExecutionUsage


class Pipeline:
    """Orchestrates ordered filter execution for request processing."""

    # Canonical built-in modules; all route configuration goes through this
    # registry rather than an endpoint-local list of filter classes.
    _REGISTRY: dict[str, Any] | None = None

    def __init__(
        self,
        filters: list[Filter] | None = None,
        *,
        stream_filter_config: Mapping[str, Any] | None = None,
    ):
        self._filters: list[Filter] = filters or []
        # Stream finalizers consume route configuration directly.  Filter
        # instances remain the request/non-streaming boundary only.
        self._stream_filter_config = dict(stream_filter_config or {})
        self._last_request_context: FilterExecutionContext | None = None

    @classmethod
    def _registry(cls) -> dict[str, Any]:
        if cls._REGISTRY is None:
            cls._REGISTRY = built_in_filter_modules()
        return cls._REGISTRY

    @classmethod
    def from_route_config(cls, route_config: dict[str, Any] | None, api_key: str | None = None) -> "Pipeline | None":
        """Build a Pipeline from canonical route ``filters`` configuration.

        Args:
            route_config: Canonical ``routes.<name>.filters`` mapping.
            api_key: Optional Bearer token from the route (inherited by filters)
        """
        if not route_config:
            return None
        # Canonical — flat filter names → configs, ordered by each module's
        # default priority or an explicit route-local ``priority`` override.
        return cls._from_simple_config(route_config, api_key=api_key)

    @classmethod
    def enabled_filter_names(cls, route_config: dict[str, Any] | None) -> list[str]:
        """Return enabled configured filters in their effective execution order."""
        if not isinstance(route_config, dict):
            return []
        registry = cls._registry()
        normalized = normalize_filters(
            route_config,
            default_priorities={name: module.request_priority for name, module in registry.items()},
        )
        validate_filter_module_settings(normalized)
        return [
            name for name, _ in sorted(
                normalized.items(), key=lambda item: item[1]["priority"]
            )
            if normalized[name]["enabled"]
        ]

    @classmethod
    def _from_simple_config(cls, route_config: dict, api_key: str | None = None) -> "Pipeline | None":
        """Build from canonical ``{name: {enabled: true, …}}`` format."""
        registry = cls._registry()
        normalized = normalize_filters(
            route_config,
            default_priorities={name: module.request_priority for name, module in registry.items()},
        )
        validate_filter_module_settings(normalized)

        filters = []
        for name, cfg in sorted(
            normalized.items(), key=lambda item: item[1]["priority"]
        ):
            if cfg["enabled"]:
                fcfg = dict(cfg, name=name)
                # Inject route-level api_key into filter config if not already set
                if api_key and fcfg.get("api_key") is None:
                    fcfg["api_key"] = api_key
                filter_instance = registry[name].request_factory(config=fcfg)
                filter_instance._route_priority = cfg["priority"]
                filters.append(filter_instance)
        # Preserve the operator-facing mapping for stream construction. The
        # request normalizer injects request-phase default priorities, whereas
        # a stream finalizer must use its own phase default unless the operator
        # explicitly supplied ``priority``.
        return cls(filters, stream_filter_config=deepcopy(route_config))

    @property
    def filters(self) -> list[Filter]:
        """Return filters sorted by priority (lowest first)."""
        return sorted(
            self._filters,
            key=lambda filter_: getattr(filter_, "_route_priority", filter_.priority),
        )

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
                _pipe.emit_process_request_error(
                    req_id=req_id, filter_name=f.__class__.__name__, error=str(e))

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
                _pipe.emit_filter_triggered(
                    req_id=req_id, action=e.action,
                    filter_name=f.__class__.__name__, message=e.message)
            except Exception as e:
                _pipe.emit_process_response_error(
                    req_id=req_id, filter_name=f.__class__.__name__, error=str(e))

        return response

    def _build_stream_finalizers(self, conversation_messages: list[dict[str, Any]] | None = None) -> list:
        """Build streaming finalizers from route filter configuration.

        Delegates to :func:`keeprollming.streaming.finalizer_factory.build_finalizers`.

        Core protocol finalizers (always included):
        - ToolCallFinalizer (priority 40) — assembles ToolCallDelta → ToolCallComplete

        Route-driven finalizers (included when their configuration is enabled):
        - ToolRewriteFinalizer (priority 15) — XML pseudo-tool-call → structured
        - TimestampFinalizer (priority 20) — tail-buffer timestamp dedup
        - NudgeContinuationFinalizer (priority 50) — lazy output detection & recovery
        - TLSFinalizer (priority 55) — tool call loop detection & intervention
        - RLSFinalizer (priority 60) — reasoning loop detection & intervention

        Returns:
            list[StreamFinalizer]: Finalizers ordered by priority (lower first).
        """
        from keeprollming.streaming.finalizer_factory import build_finalizers
        return build_finalizers(
            self._stream_filter_config,
            conversation_messages=conversation_messages,
        )

    async def run_stream(
        self,
        payload: dict[str, Any],
        req_id: str,
        upstream_model: str,
        route_name: str = "",
        upstream_url: str = "",
        upstream_stream=None,
        dispatcher: Any = None,
    ) -> AsyncIterator[bytes]:
        """Run the event-driven streaming pipeline with finalizers.

        Flow:
        1. Request filters
        2. Build finalizers from enabled filters
        3. Run the parser → finalizers → serializer pipeline
        4. Yield serialized SSE bytes

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
        from keeprollming.streaming.serializer import OpenAISSESerializer
        from keeprollming.streaming.runner import run_stream

        _pipe.emit_stream_started(
            req_id=req_id, has_upstream=upstream_stream is not None)

        # Phase 1: request filters
        payload = await self.process_request(
            payload, req_id, upstream_model, route_name, upstream_url
        )

        _pipe.emit_stream_request_filters_done(
            req_id=req_id, has_upstream=upstream_stream is not None)

        # Phase 2: Build finalizers from enabled filters
        finalizers = self._build_stream_finalizers(payload.get("messages"))

        _pipe.emit_stream_finalizers_built(
            req_id=req_id, finalizer_count=len(finalizers))

        # Phase 3: run the canonical pipeline with recovery support.
        async def _stream_upstream(upstream_payload):
            """Wrapper for the upstream stream."""
            if upstream_stream:
                async for chunk in upstream_stream(upstream_payload):
                    yield chunk

        # B2/C2A: Build upstream_factory for recovery attempts.
        # The factory accepts a payload argument so the runner can pass an
        # augmented payload (with request_payload_patch applied) on recovery.
        upstream_factory = None
        if upstream_stream:
            upstream_factory = lambda p: _stream_upstream(p)

        serializer = OpenAISSESerializer()
        # Pass the same ExecutionUsage object into
        # run_stream() so the caller retains the reference after the
        # generator completes.
        _execution_usage = ExecutionUsage.empty()

        upstream_events = run_stream(
            upstream_chunks=_stream_upstream(payload),
            finalizers=finalizers,
            serializer=serializer,
            upstream_factory=upstream_factory,
            payload=payload,
            execution_usage=_execution_usage,
            dispatcher=dispatcher,
            req_id=req_id,
        )

        # Wrapper generator to finalize execution_usage after iteration
        async def _stream_with_usage():
            nonlocal _execution_usage
            # The HTTP handler owns keepalive and its pending read task.  A
            # second wrapper here used to create nested readers for the same
            # upstream stream, which obscured cancellation ownership.
            async for chunk in upstream_events:
                yield chunk
            # After iteration, finalize the execution usage (update counters)
            _execution_usage.finalize()

        # Yield from wrapper
        async for chunk in _stream_with_usage():
            yield chunk

        # Expose execution_usage to caller via Pipeline instance attribute
        self._execution_usage = _execution_usage

        _pipe.emit_stream_completed(
            req_id=req_id,
            execution_usage=_execution_usage is not None)

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
