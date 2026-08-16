"""
Filter Pipeline Architecture for LLM Orchestrator.

This module provides a flexible, extensible filter system that allows
manipulation of requests and responses at various points in the
orchestration pipeline.

Architecture:
- Filter: Abstract base class for all filters
- FilterExecutionContext: Manages state across filter invocations

Runtime configuration and execution are owned by :class:`Pipeline` in
``orchestrator.pipeline``. ``FilterChain`` remains a small direct-composition
test helper; it does not parse route configuration and is not a runtime path.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from keeprollming.logger import log


# ── Base classes ────────────────────────────────────────────────────────────


@dataclass
class FilterConfig:
    """Base configuration for all filters."""
    enabled: bool = True
    name: Optional[str] = None

    def __post_init__(self):
        if self.name is None:
            self.name = self.__class__.__name__


@dataclass
class StreamingResponse:
    """Concrete response DTO for the filter chain.

    Used by Pipeline and streaming handlers to wrap accumulated
    response data before passing it to response-phase filters.
    Satisfies the Response Protocol.

    Replaces the 4 scattered MockStreamingResponse / MockResponse
    local class definitions across pipeline.py, streaming_handlers.py,
    chat_completions.py, and tests.
    """
    content: str = ""
    model: str = ""
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None
    reasoning_content: str = ""


class Request(Protocol):
    """Protocol for request objects that can be processed by filters."""
    messages: List[Dict[str, Any]]
    model: str
    stream: bool
    metadata: Dict[str, Any]


class Response(Protocol):
    """Protocol for response objects that can be processed by filters."""
    content: str
    model: str
    usage: Optional[Dict[str, int]]
    finish_reason: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]


class FilterExecutionContext:
    """
    Context object that maintains state across filter invocations.

    This allows filters to share data and maintain state when needed.
    """

    def __init__(self, req_id: Optional[str] = None, upstream_payload: Optional[Dict[str, Any]] = None, route_name: Optional[str] = None, is_streaming_post_process: bool = False, upstream_model: Optional[str] = None, upstream_url: Optional[str] = None, event_dispatcher: Any = None):
        self.state: Dict[str, Any] = {}
        self.request_history: List[Dict[str, Any]] = []
        self.response_history: List[Dict[str, Any]] = []
        self.current_filter: Optional[str] = None
        self.is_streaming_post_process = is_streaming_post_process
        self.req_id = req_id
        self.upstream_payload = upstream_payload or {}
        self.route_name = route_name
        self.upstream_model = upstream_model
        self.upstream_url = upstream_url
        self.event_dispatcher = event_dispatcher
        self.metadata: Dict[str, Any] = {
            "nudge_attempts": 0,
            "loop_detection": {"duplicates": 0, "last_response_hash": None},
        }
        # Set by Pipeline — allows filters to make upstream calls
        self._upstream_caller: Any = None

    def add_request_history(self, request: Request) -> None:
        """Track request for stateful filters (e.g., loop detector)."""
        self.request_history.append({
            "messages": request.messages,
            "model": request.model,
            "timestamp": len(self.request_history),  # Simple counter instead of datetime
        })

    def add_response_history(self, response: Response) -> None:
        """Track response for stateful filters (e.g., loop detector)."""
        self.response_history.append({
            "content": response.content,
            "model": response.model,
            "finish_reason": response.finish_reason,
            "tool_calls": response.tool_calls,
            "timestamp": len(self.response_history),
        })

    def increment_nudge_attempts(self, max_attempts: int) -> bool:
        """
        Increment nudge attempt counter and check if we should continue.

        Args:
            max_attempts: Maximum allowed nudge attempts before giving up

        Returns:
            True if should proceed with nudge (under limit), False if exceeded
        """
        self.metadata["nudge_attempts"] = self.metadata.get("nudge_attempts", 0) + 1
        # Return True if under max, False if at or over limit
        return self.metadata["nudge_attempts"] <= max_attempts

    def reset_nudge_attempts(self) -> None:
        """Reset nudge attempt counter (e.g., after successful response)."""
        self.metadata["nudge_attempts"] = 0

    def get_recent_responses(self, count: int = 10) -> List[Dict[str, Any]]:
        """
        Get most recent responses from history.

        Args:
            count: Number of recent responses to retrieve (default: 10)

        Returns:
            List of the most recent response entries in chronological order
        """
        if not self.response_history:
            return []
        
        # Return last 'count' responses in original order (oldest first among recent)
        return self.response_history[-count:]

    def clear_history(self) -> None:
        """Clear all request and response history."""
        self.request_history.clear()
        self.response_history.clear()


class Filter(ABC):
    """
    Abstract base class for all filters.

    Filters can process requests before they're sent to the LLM,
    and responses after they're received. They can modify data or
    raise StopFilterChain to trigger regeneration.
    """

    # ── Class-level capabilities ────────────────────────────────────

    supports_streaming: bool = True
    supports_non_streaming: bool = True
    priority: int = 50  # Lower = earlier execution

    # ── Instance ─────────────────────────────────────────────────────

    _default_name: str = "base_filter"

    def __init__(self, config: Optional[FilterConfig] = None):
        self.config = config or FilterConfig(name=self._default_name)
        # Store instance name only if different from class default
        if self.config.name and self.config.name != self._default_name:
            self._instance_name = self.config.name

    @property
    def state(self) -> Dict[str, Any]:
        """Get filter-specific state."""
        return self._state

    @state.setter
    def state(self, value: Dict[str, Any]) -> None:
        """Set filter-specific state."""
        self._state = value

    @property
    def name(self) -> str:
        """Get the filter's instance name (from config or class)."""
        # Return instance name if set, otherwise use class default
        return getattr(self, '_instance_name', None) or self.__class__._default_name

    @property
    def is_enabled(self) -> bool:
        """Check if this filter is enabled."""
        return self.config.enabled

    @abstractmethod
    async def process_request(self, request: Request, context: FilterExecutionContext) -> Request:
        """
        Process a request before it's sent to the LLM.

        Args:
            request: The incoming request to process
            context: Shared execution context

        Returns:
            Modified request (or same request if no changes needed)
        """
        pass

    @abstractmethod
    async def process_response(self, response: Response, context: FilterExecutionContext) -> Response:
        """
        Process a response after it's received from the LLM.

        Args:
            response: The outgoing response to process
            context: Shared execution context

        Returns:
            Modified response (or same response if no changes needed)

        Raises:
            StopFilterChain: If filter wants to stop processing and trigger regeneration
        """
        pass

    def on_enable(self) -> None:
        """Called when filter is enabled. Override for initialization."""
        pass

    def on_disable(self) -> None:
        """Called when filter is disabled. Override for cleanup."""
        pass

    def reset(self) -> None:
        """Reset any internal state. Override if needed."""
        pass

    # ── Shared Utility Methods (used by TLS, nudge, and other filters) ──────

    def _resolve_upstream_url(self, context: FilterExecutionContext) -> str:
        """Resolve upstream_url: context attribute > filter config > metadata."""
        return (
            getattr(context, 'upstream_url', None)
            or getattr(self, '_upstream_url', '')
            or context.metadata.get("upstream_url", "")
        )

    def _resolve_upstream_model(self, context: FilterExecutionContext) -> Optional[str]:
        """Resolve upstream_model from context."""
        return getattr(context, 'upstream_model', None) or context.metadata.get("upstream_model")

    def _resolve_req_id(self, context: FilterExecutionContext) -> str:
        """Generate or extract a request ID for logging."""
        import uuid
        return (
            context.req_id
            if context.req_id and context.req_id != "-"
            else str(uuid.uuid4())[:8]
        )

    def _get_conversation(self, context: FilterExecutionContext) -> list:
        """Extract conversation history from context metadata or payload."""
        import copy
        conv = context.metadata.get("conversation_history", []) or []
        if not conv:
            payload = context.upstream_payload or {}
            conv = copy.deepcopy(payload.get("messages", []))
        return conv or []

    async def _make_http_retry(
        self,
        messages: list,
        model: str,
        upstream_url: str,
        api_key: Optional[str] = None,
        timeout: int = 120,
        original_payload: Optional[dict] = None,
    ) -> Any:
        """Make an HTTP retry request to the upstream model.

        Uses prepare_upstream_request() from keeprollming.upstream
        to include all inference parameters from the original payload
        (temperature, max_tokens, top_p, overrides, etc.).

        Args:
            messages: Conversation messages to send
            model: Model name for the request
            upstream_url: Base upstream URL
            api_key: Optional API key
            timeout: Request timeout in seconds
            original_payload: Original request payload for parameter inheritance
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
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception:
            # Return None on any error (connection refused, timeout, etc.)
            # Filters handle None gracefully
            return None

    def _log_filter_executed(
        self,
        req_id: str,
        filters: list,
        status: str = "complete",
        **extra,
    ) -> None:
        """Log filter_chain_executed event (consistent format across all filters)."""
        log(
            "INFO",
            "filter_chain_executed",
            req_id=req_id,
            filters=filters,
            status=status,
            **extra,
        )


class StopFilterChain(Exception):
    """
    Exception raised by filters to stop the pipeline and trigger regeneration.

    Usage in process_response:
        if should_regenerate(response):
            raise StopFilterChain("Triggering regeneration")
    """

    def __init__(self, message: str, action: str = "regenerate", nudge_message: Optional[str] = None):
        self.message = message
        self.action = action  # "regenerate" or "nudge"
        self.nudge_message = nudge_message  # Optional custom message for nudge action
        super().__init__(message)


class FilterChain:
    """Direct-composition executor retained for focused filter tests.

    Production code uses :class:`keeprollming.orchestrator.pipeline.Pipeline`.
    This class intentionally accepts already-instantiated filters only, so it
    cannot become a second route-configuration parser.
    """

    def __init__(self, filters: List[Filter], execution_order: List[str]):
        """
        Initialize filter chain.

        Args:
            filters: List of filter instances to include
            execution_order: Ordered list of filter names defining execution sequence
        """
        self.filters = {f.name: f for f in filters}
        self.execution_order = execution_order
        self._validate_order()

    def _validate_order(self) -> None:
        """Validate that all filters in order exist."""
        missing = set(self.execution_order) - set(self.filters.keys())
        if missing:
            raise ValueError(
                f"Filter(s) in execution order not found: {missing}. "
                f"Available filters: {list(self.filters.keys())}"
            )

    def get_enabled_filters(self) -> List[Filter]:
        """Get filters in execution order, filtering out disabled ones."""
        return [
            self.filters[name]
            for name in self.execution_order
            if self.filters[name].is_enabled
        ]

    async def process_request(
        self,
        request: Request,
        context: FilterExecutionContext
    ) -> Request:
        """
        Process request through all enabled filters in order.

        Args:
            request: Incoming request to process
            context: Shared execution context

        Returns:
            Modified request after all filters processed
        """
        for filter_obj in self.get_enabled_filters():
            context.current_filter = filter_obj.name
            request = await filter_obj.process_request(request, context)

        return request

    async def process_response(
        self,
        response: Response,
        context: FilterExecutionContext
    ) -> Response:
        """
        Process response through all enabled filters in order.

        Args:
            response: Outgoing response to process
            context: Shared execution context

        Returns:
            Modified response after all filters processed

        Raises:
            StopFilterChain: If any filter raises this exception
        """
        nudge_count = 0
        loop_count = 0
        
        for filter_obj in self.get_enabled_filters():
            context.current_filter = filter_obj.name
            try:
                response = await filter_obj.process_response(response, context)
                
                # Track nudges and loops from metadata
                if context.metadata.get("nudge_attempts", 0) > nudge_count:
                    nudge_count = context.metadata["nudge_attempts"]
                    
            except StopFilterChain as e:
                # Emit filter error event
                from keeprollming.orchestrator.filters.events import emit_filter_error
                emit_filter_error(
                    context,
                    error_type="StopFilterChain",
                    message=e.message,
                )
                
                # Track filter type for stats
                if "nudge" in str(e).lower() or "lazy" in str(e).lower():
                    nudge_count += 1
                elif "loop" in str(e).lower():
                    loop_count += 1
                
                # Re-raise with current filter context - preserve nudge_message!
                raise StopFilterChain(
                    f"[{filter_obj.name}] {e.message}",
                    action=e.action,
                    nudge_message=getattr(e, 'nudge_message', None)
                )

        # Emit filter chain executed event
        enabled_filters = self.get_enabled_filters()
        if enabled_filters:
            from keeprollming.orchestrator.filters.events import emit_filter_chain_executed
            emit_filter_chain_executed(
                context,
                filters_executed=[f.name for f in enabled_filters],
                total_filters=len(enabled_filters),
                nudge_count=nudge_count,
                loop_count=loop_count,
            )

        return response

    def add_filter(self, filter_obj: Filter) -> None:
        """Add a filter to the chain (not in execution order)."""
        self.filters[filter_obj.name] = filter_obj

    def remove_filter(self, name: str) -> bool:
        """Remove a filter from the chain."""
        if name in self.filters:
            del self.filters[name]
            return True
        return False

    def reset_all_filters(self) -> None:
        """Reset state for all filters in the chain."""
        for filter_obj in self.filters.values():
            if hasattr(filter_obj, 'reset'):
                filter_obj.reset()
