"""Request-scoped reasoning-loop detection and bounded non-streaming recovery."""

from dataclasses import dataclass

from keeprollming.filters.loop_recovery import recover, retry_limit
from keeprollming.filters.tool_loop_stopper.detection import signature
from keeprollming.orchestrator.filter import Filter, FilterConfig, StreamingResponse


@dataclass
class ReasoningLoopStopperConfig(FilterConfig):
    max_retries: int = 2
    retry_timeout: float | None = None
    max_repeats: int = 1
    rls_message: str = "You have already used this exact reasoning. Please proceed with a different approach or stop thinking about it."
    intervention_message: str = "I notice I'm repeating the same reasoning. I will try a different approach."
    fallback_message: str = "I notice I'm repeating the same reasoning. Let me try a different approach."
    fallback_streaming_message: str = "I seem to be thinking in circles. I will pause and reconsider."
    send_user_message: bool = True

    def __post_init__(self):
        super().__post_init__()
        self.name = "reasoning_loop_stopper"


def _last_reasoning(messages):
    for message in reversed(messages):
        if message.get("role") == "assistant":
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            if reasoning:
                return reasoning
    return None


def _call_signatures(message):
    return {
        signature((call.get("function") or {}).get("name", ""), (call.get("function") or {}).get("arguments", "{}"))
        for call in message.get("tool_calls") or []
    }


class ReasoningLoopStopperFilter(Filter):
    priority = 22
    _default_name = "reasoning_loop_stopper"

    def __init__(self, config):
        settings = config if isinstance(config, dict) else {"enabled": bool(config)}
        fields = ReasoningLoopStopperConfig.__dataclass_fields__
        values = {key: value for key, value in settings.items() if key in fields}
        values["max_retries"] = retry_limit(settings)
        super().__init__(ReasoningLoopStopperConfig(**values))
        self._upstream_url = settings.get("upstream_url", "")
        self._api_key = settings.get("api_key")

    async def process_request(self, request, context):
        return request

    def _get_last_reasoning(self, conv):
        return _last_reasoning(conv)

    def _is_loop(self, response, history):
        reasoning = getattr(response, "reasoning_content", "") or ""
        if not reasoning or self.config.max_repeats <= 0:
            return False
        assistants = [message for message in history if message.get("role") == "assistant"]
        # Internal RLS corrections must not erase the preceding reasoning/call.
        previous = next(
            (
                message
                for message in reversed(assistants)
                if message.get("reasoning_content") or message.get("reasoning")
            ),
            None,
        )
        if previous is None:
            return False
        current_calls = _call_signatures({"tool_calls": getattr(response, "tool_calls", None)})
        previous_calls = _call_signatures(previous)
        if current_calls and previous_calls and current_calls != previous_calls:
            return False
        repeats = 0
        for message in reversed(assistants):
            text = message.get("reasoning_content") or message.get("reasoning")
            if text is None:
                continue
            if text.strip() != reasoning.strip():
                break
            repeats += 1
        return repeats >= self.config.max_repeats

    async def process_response(self, response, context):
        if context.metadata.get("rls_cleared"):
            return response
        return await recover(
            self,
            response,
            context,
            self._is_loop,
            self._intervention,
            lambda result, detected: self._handle_fallback(result, context.req_id, context),
        )

    def _intervention(self, history, response, detected):
        history.append({"role": "assistant", "content": self.config.intervention_message})
        if self.config.send_user_message:
            history.append({"role": "user", "content": self.config.rls_message})
        return history

    def _handle_fallback(self, response, req_id, context):
        return StreamingResponse(
            content=self.config.fallback_message or "Stopped repeating reasoning.",
            model=response.model,
            finish_reason="stop",
        )

    def reset(self):
        pass
