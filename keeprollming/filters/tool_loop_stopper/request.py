"""
Tool Loop Stopper (TLS) Filter - Detects and breaks LLM tool call loops.

When a model emits the same tool call it just executed (identical function name and arguments),
this filter intervenes by injecting a TLS tool result telling the model to move on.

Architecture:
1. Extract last tool call from conversation history
2. Compare with tool call in current model response
3. If identical → inject TLS tool result, call upstream again
4. Return reconstructed response without the repeated tool call or TLS message
5. If model repeats again after TLS → return fallback message

Priority: 25 (runs BEFORE nudge filter at 50)

Streaming is handled by the stream finalizer pipeline.
"""

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from keeprollming.filters.loop_recovery import recover, retry_limit
from keeprollming.logger import log
from keeprollming.orchestrator.filter import (
    Filter,
    FilterConfig,
    FilterExecutionContext,
    Request,
    Response,
    StreamingResponse,
)
from keeprollming.orchestrator.filters.events import (
    emit_tls_fallback,
)

from .detection import repeats, signature

# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class ToolLoopStopperConfig(FilterConfig):
    """Configuration for the TLS filter."""

    max_retries: int = 2
    retry_timeout: float | None = None
    nudge_message: str = "Do NOT call the same tool again. Proceed with a different action."
    fuzzy_threshold: float | None = None

    max_repeats: int = 1
    """Number of consecutive identical tool calls that trigger exact detection.
    0 = disabled. 1 = fire on first repeat (current behavior)."""

    tls_message: str | None = (
        "You've already executed this tool call with the same arguments. "
        "Please proceed with the next step or try a different approach."
    )
    fallback_message: str | None = "I notice I'm repeating the same tool call. Let me try a different approach."
    trigger_patterns: List[str] = field(default_factory=list)

    # ── Fuzzy detection ────────────────────────────────────────────────
    fuzzy_max_repeats: int = 0
    """If >0, detect same tool call (same function+args) called N+ times
    across the look-back window (consecutive or non-consecutive)."""
    fuzzy_look_back: int = 0
    """How many recent assistant messages to scan for fuzzy counting.
    0 = scan the entire conversation history."""

    ab_loop_detection: bool = False
    """If True, detect alternating A,B,A,B patterns with full signature match."""
    send_user_message: bool = True
    """If True, inject a user role message (stronger) in addition to the tool result."""
    fallback_template: str | None = (
        "The system automatically stopped repeated tool call: {name}({args}). Please try a different approach."
    )
    """Template for fallback message. {name} and {args} are substituted."""

    fallback_streaming_message: str = "I seem to be going in circles. I will pause and reconsider."
    """Fallback content yielded to client after max_retries exhausted in streaming."""

    def __post_init__(self):
        super().__post_init__()
        self.name = "model_tool_loop_stopper"
        if not self.tls_message:
            self.tls_message = "You've already executed this tool call with the same arguments. Please proceed with the next step or try a different approach."
        if not self.fallback_message:
            self.fallback_message = "I notice I'm repeating the same tool call. Let me try a different approach."


# ── Filter Implementation ─────────────────────────────────────────────────────


class ToolLoopStopperFilter(Filter):
    """Detect and break tool call loops.

    The TLS finalizer owns streaming loop intervention.
    """

    _default_name = "model_tool_loop_stopper"
    priority = 25  # Before nudge (50)

    def __init__(self, config=None, **kwargs):
        # Support both: ToolLoopStopperFilter(filter_cfg_dict) from from_route_config
        #           and ToolLoopStopperFilter(enabled=True, ...) from tests
        if config is None:
            config = {}
        if isinstance(config, dict):
            enabled = config.get("enabled", kwargs.get("enabled", True))
            tls_message = config.get("tls_message", kwargs.get("tls_message", None))
            fallback_message = config.get("fallback_message", kwargs.get("fallback_message", None))
            max_repeats = config.get(
                "max_repeats",
                kwargs.get("max_repeats", 1),
            )
            trigger_patterns = config.get("trigger_patterns", kwargs.get("trigger_patterns", None))
            fuzzy_max_repeats = config.get("fuzzy_max_repeats", kwargs.get("fuzzy_max_repeats", 0))
            fuzzy_look_back = config.get("fuzzy_look_back", kwargs.get("fuzzy_look_back", 0))
            send_user_message = config.get("send_user_message", kwargs.get("send_user_message", True))
            fallback_template = config.get("fallback_template", kwargs.get("fallback_template", None))
            ab_loop_detection = config.get("ab_loop_detection", kwargs.get("ab_loop_detection", False))
        else:
            enabled = config
            tls_message, fallback_message, trigger_patterns = None, None, None
            max_repeats, fuzzy_max_repeats, fuzzy_look_back = 1, 0, 0
            send_user_message, fallback_template, ab_loop_detection = True, None, False

        filter_config = ToolLoopStopperConfig(
            enabled=enabled,
            tls_message=tls_message
            or "You've already executed this tool call with the same arguments. Please proceed with the next step or try a different approach.",
            fallback_message=fallback_message
            or "I notice I'm repeating the same tool call. Let me try a different approach.",
            max_repeats=max_repeats,
            trigger_patterns=trigger_patterns or [],
            fuzzy_max_repeats=fuzzy_max_repeats,
            fuzzy_look_back=fuzzy_look_back,
            ab_loop_detection=ab_loop_detection,
            send_user_message=send_user_message,
            fallback_template=fallback_template,
            max_retries=retry_limit(config) if isinstance(config, dict) else 2,
            retry_timeout=config.get("retry_timeout") if isinstance(config, dict) else None,
            nudge_message=config.get(
                "nudge_message", "Do NOT call the same tool again. Proceed with a different action."
            )
            if isinstance(config, dict)
            else "Proceed differently.",
            fuzzy_threshold=config.get("fuzzy_threshold") if isinstance(config, dict) else None,
            fallback_streaming_message=config.get(
                "fallback_streaming_message",
                "I seem to be going in circles. I will pause and reconsider.",
            )
            if isinstance(config, dict)
            else "I seem to be going in circles. I will pause and reconsider.",
        )
        super().__init__(filter_config)
        self._upstream_url = config.get("upstream_url", "") if isinstance(config, dict) else ""
        self._api_key = config.get("api_key") if isinstance(config, dict) else None

        # Streaming detection state
        self._tool_call_buffer: List[Dict[str, Any]] = []
        self._buffering_tool_calls = False

    def _get_tc_signature(self, tc: Dict[str, Any]) -> Tuple[str, str]:
        """Extract (function_name, canonical_args) from a tool call dict."""
        fn = tc.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        try:
            canon_args = json.dumps(json.loads(raw_args), sort_keys=True)
        except json.JSONDecodeError:
            canon_args = raw_args
        return (name, canon_args)

    def _detect_ab_loop(self, conv: List[Dict[str, Any]], current_tc: Dict[str, Any]) -> bool:
        """Detect alternating A,B,A,B pattern in conversation + current tool call.

        Checks if the last 3+ assistant tool calls + the current one form
        an alternating pattern (A, B, A, ...). Full signature match (name+args).
        """
        current_sig = self._get_tc_signature(current_tc)

        # Collect last tool call signatures from conversation (chronological)
        recent_sigs: List[Tuple[str, str]] = []
        for msg in conv:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    recent_sigs.append(self._get_tc_signature(tc))

        # Need at least 3 prior calls + current = 4 to detect A,B,A,B
        if len(recent_sigs) < 3:
            return False

        # Take last 3 signatures from conversation
        last_3 = recent_sigs[-3:]
        # With current, the sequence is [last_3[0], last_3[1], last_3[2], current_sig]
        # A,B,A,B pattern: sig[0]==sig[2] AND sig[1]==current
        if last_3[0] == last_3[2] and last_3[1] == current_sig:
            return True

        return False

    def _matches_last_tool_call(
        self,
        current_name: str,
        current_args: str,
        last_tool: Optional[Tuple[str, str]],
    ) -> bool:
        """Check if current tool call matches the last executed one."""
        if last_tool is None:
            return False
        last_name, last_args = last_tool
        if current_name != last_name:
            return False
        try:
            current_canon = json.dumps(json.loads(current_args), sort_keys=True)
            return current_canon == last_args
        except json.JSONDecodeError:
            return current_args == last_args

    def _extract_tool_calls_from_response(self, response: Any) -> List[Dict[str, Any]]:
        """Extract tool_calls from a response object."""
        try:
            if hasattr(response, "tool_calls"):
                return response.tool_calls
            choices = response.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                return msg.get("tool_calls", [])
        except (AttributeError, TypeError, IndexError):
            pass
        return []

    # ── HTTP Retry ────────────────────────────────────────────────────────

    # _make_http_retry inherited from Filter base class

    # ── Abstract Method ──────────────────────────────────────────────────

    async def process_request(self, request: Request, context: FilterExecutionContext) -> Request:
        """Pass through — TLS only modifies responses."""
        return request

    def _get_signatures_from_conv(self, conv: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
        """Extract all tool call signatures from conversation in chronological order."""
        sigs: List[Tuple[str, str]] = []
        for msg in conv:
            if msg.get("role") == "assistant":
                for tc in msg.get("tool_calls") or []:
                    sigs.append(self._get_tc_signature(tc))
        return sigs

    def _count_consecutive_from_end(self, sigs: List[Tuple[str, str]], target_sig: Tuple[str, str]) -> int:
        """Count consecutive tool calls from the END of sigs that match target_sig."""
        count = 0
        for sig in reversed(sigs):
            if sig == target_sig:
                count += 1
            else:
                break
        return count

    def _count_fuzzy_in_window(self, sigs: List[Tuple[str, str]], target_sig: Tuple[str, str], look_back: int) -> int:
        """Count total matching tool calls within the look-back window (end of sigs).

        look_back = 0 means scan all.
        """
        if look_back > 0:
            sigs = sigs[-look_back:]
        return sum(1 for sig in sigs if sig == target_sig)

    # ── Streaming buffer helpers ────────────────────────────────────────

    def check_tool_calls_for_loop(
        self,
        tool_calls: List[Dict[str, Any]],
        conv: List[Dict[str, Any]],
    ) -> tuple[bool, Dict[str, Any] | None, tuple[str, str] | None]:
        """Check if ANY tool call in the list is a loop.

        Returns:
            (is_loop, triggering_tool_call, triggering_signature)
            where triggering_tool_call is the first tool call that matched
            a loop pattern.
        """
        prior = [signature(name, args) for name, args in self._get_signatures_from_conv(conv)]
        for call in tool_calls or []:
            function = call.get("function") or {}
            current = signature(function.get("name", ""), function.get("arguments", "{}"))
            if repeats(
                prior,
                current,
                max_repeats=self.config.max_repeats,
                fuzzy_max_repeats=self.config.fuzzy_max_repeats,
                fuzzy_look_back=self.config.fuzzy_look_back,
                fuzzy_threshold=self.config.fuzzy_threshold,
                ab_loop_detection=self.config.ab_loop_detection,
            ):
                return True, call, self._get_tc_signature(call)
            prior.append(current)
        return False, None, None

    async def process_response(self, response, context):
        if context.metadata.get("tls_cleared"):
            return response
        return await recover(
            self,
            response,
            context,
            self._is_loop,
            self._intervention,
            lambda result, detected: self._handle_fallback(
                result,
                context.req_id,
                (detected.get("function") or {}).get("name", ""),
                context,
            ),
        )

    def _is_loop(self, response, history):
        accepted, rejected = [], []
        for call in getattr(response, "tool_calls", None) or []:
            prior = [*history, {"role": "assistant", "tool_calls": accepted}]
            detected, _, _ = self.check_tool_calls_for_loop([call], prior)
            (rejected if detected else accepted).append(call)
        if rejected and accepted:
            response.tool_calls = accepted
            return None
        return rejected[0] if rejected else None

    def _intervention(self, history, response, detected):
        # Every synthetic result must follow a matching assistant tool call.
        calls = copy.deepcopy(response.tool_calls or [])
        for call in calls:
            call.setdefault("id", f"krm-withheld-{uuid.uuid4().hex}")
        history.append({"role": "assistant", "content": response.content or None, "tool_calls": calls})
        for call in calls:
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": self.config.tls_message
                    if self._get_tc_signature(call) == self._get_tc_signature(detected)
                    else "This call was withheld during loop recovery and was not executed.",
                }
            )
        if self.config.send_user_message:
            history.append({"role": "user", "content": self.config.nudge_message})
        return history

    def _format_fallback_message(self, req_id: str, function_name: str, function_args: str = "") -> str:
        """Format fallback message with function name substitution."""
        args_preview = function_args[:80] if function_args else ""
        if self.config.fallback_template:
            try:
                return self.config.fallback_template.format(
                    name=function_name,
                    args=args_preview,
                )
            except (KeyError, ValueError):
                pass
        return self.config.fallback_message or f"Stopped repeating tool call: {function_name}"

    def _handle_fallback(
        self, response: Response, req_id: str, function_name: str = "", context: Any = None
    ) -> Response:
        """Model repeated tool call after TLS intervention — return fallback."""
        fallback_content = self.config.fallback_message or "Stopped repeating tool call"
        if function_name:
            fallback_content = self._format_fallback_message(req_id, function_name)

        log(
            "INFO",
            "tls_fallback",
            req_id=req_id,
            reason="model_repeated_after_tls",
            function_name=function_name,
        )
        if context is not None:
            emit_tls_fallback(context, reason="model_repeated_after_tls")
        self._log_filter_executed(req_id, ["model_tool_loop_stopper"], status="fallback")
        return StreamingResponse(
            content=fallback_content or "Stopped repeating tool call", model=response.model, finish_reason="stop"
        )

    def reset(self) -> None:
        """Reset any internal state between requests."""
        pass
