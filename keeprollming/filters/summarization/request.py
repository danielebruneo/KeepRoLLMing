"""
SummarizationFilter — handles conversation summarization as a pipeline filter.

Checks if conversation exceeds context limits and repacks messages
with a generated summary. Runs BEFORE the upstream call.

Priority=15 — after SystemPrompt (10), before ToolRewrite (20).
"""

from keeprollming.orchestrator.filter import (
    Filter,
    FilterExecutionContext,
    Request,
    Response,
)
from keeprollming.logger import log


class SummarizationFilter(Filter):
    """Checks if summarization is needed and repacks messages.

    Requires the Route object to be passed via context metadata:
        context.metadata["route"] = route
        context.metadata["plan"] = plan
    """

    priority: int = 15
    name: str = "summarization"
    supports_streaming: bool = True
    supports_non_streaming: bool = True

    def __init__(self, config=None):
        """Initialize SummarizationFilter, accepting dict or FilterConfig."""
        from keeprollming.orchestrator.filter import FilterConfig
        if isinstance(config, dict):
            base_config = {"enabled": config.get("enabled", True)}
            super().__init__(FilterConfig(**base_config))
            self._config_dict = config
        else:
            super().__init__(config)
            self._config_dict = {}

    async def process_request(
        self, request: Request, context: FilterExecutionContext
    ) -> Request:
        """Check if summarization is needed and repack messages if so."""
        route = context.metadata.get("route")
        plan = context.metadata.get("plan")
        if route is None or plan is None:
            return request

        is_passthrough = bool(getattr(route, 'passthrough_enabled', False))
        is_summary_enabled = bool(getattr(route, 'summary_enabled', True))
        if is_passthrough or not is_summary_enabled or not getattr(plan, 'should', False):
            return request

        upstream_model = context.upstream_model or ""
        summary_model = getattr(route, 'summary_model', None) or upstream_model

        try:
            from keeprollming.processing import _execute_summarization

            repacked, did_summarize, summary_tokens = await _execute_summarization(
                req_id=context.req_id or "unknown",
                messages=list(request.messages),
                plan=plan,
                summary_model=summary_model,
                custom_prompt_type=context.metadata.get("custom_prompt_type"),
                custom_prompt_text=context.metadata.get("custom_prompt_text"),
                user_id=context.upstream_payload.get("user_id", ""),
                conv_id=context.upstream_payload.get("conv_id", ""),
                pinned_head_n=context.metadata.get("pinned_head_n", 1),
                ctx_eff=context.metadata.get("ctx_eff", 8192),
                is_summary_enabled=is_summary_enabled,
            )

            if did_summarize:
                request.messages = repacked
                context.metadata["did_summarize"] = True
                context.metadata["summary_tokens"] = summary_tokens
                log("INFO", "summarization_filter_applied",
                    req_id=context.req_id,
                    original_count=len(context.upstream_payload.get("messages", [])),
                    repacked_count=len(repacked),
                )
        except Exception as e:
            log("ERROR", "summarization_filter_error",
                req_id=context.req_id, error=str(e))

        return request

    async def process_response(
        self, response: Response, context: FilterExecutionContext
    ) -> Response:
        return response
