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

import asyncio
import copy
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from keeprollming.orchestrator.filter import (
    FilterConfig,
    FilterExecutionContext,
    Request,
    Response,
    StreamingResponse,
    Filter,
)
from keeprollming.orchestrator.filters.events import (
    emit_tool_loop_detected,
    emit_tls_intervention,
    emit_tls_retry,
    emit_tls_fallback,
)
from keeprollming.logger import log


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class ToolLoopStopperConfig(FilterConfig):
    """Configuration for the TLS filter."""

    max_retries: int = 2
    retry_timeout: int = 120

    max_repeats: int = 1
    """Number of consecutive identical tool calls that trigger exact detection.
    0 = disabled. 1 = fire on first repeat (current behavior)."""

    tls_message: str | None = (
        "You've already executed this tool call with the same arguments. "
        "Please proceed with the next step or try a different approach."
    )
    fallback_message: str | None = (
        "I notice I'm repeating the same tool call. Let me try a different approach."
    )
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
        "The system automatically stopped repeated tool call: {name}({args}). "
        "Please try a different approach."
    )
    """Template for fallback message. {name} and {args} are substituted."""

    fallback_streaming_message: str = (
        "I seem to be going in circles. I will pause and reconsider."
    )
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
            max_repeats = config.get("max_repeats",
                           config.get("max_attempts",
                           kwargs.get("max_repeats",
                           kwargs.get("max_attempts", 1))))
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
            tls_message=tls_message or "You've already executed this tool call with the same arguments. Please proceed with the next step or try a different approach.",
            fallback_message=fallback_message or "I notice I'm repeating the same tool call. Let me try a different approach.",
            max_repeats=max_repeats,
            trigger_patterns=trigger_patterns or [],
            fuzzy_max_repeats=fuzzy_max_repeats,
            fuzzy_look_back=fuzzy_look_back,
            ab_loop_detection=ab_loop_detection,
            send_user_message=send_user_message,
            fallback_template=fallback_template,
            max_retries=config.get("max_retries", 2) if isinstance(config, dict) else 2,
            fallback_streaming_message=config.get("fallback_streaming_message",
                "I seem to be going in circles. I will pause and reconsider.") if isinstance(config, dict) else "I seem to be going in circles. I will pause and reconsider.",
        )
        super().__init__(filter_config)
        self._upstream_url = config.get("upstream_url", "") if isinstance(config, dict) else ""

        # Streaming detection state
        self._tool_call_buffer: List[Dict[str, Any]] = []
        self._buffering_tool_calls = False



    def _get_tc_signature(
        self, tc: Dict[str, Any]
    ) -> Tuple[str, str]:
        """Extract (function_name, canonical_args) from a tool call dict."""
        fn = tc.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        try:
            canon_args = json.dumps(json.loads(raw_args), sort_keys=True)
        except json.JSONDecodeError:
            canon_args = raw_args
        return (name, canon_args)

    def _detect_ab_loop(
        self, conv: List[Dict[str, Any]], current_tc: Dict[str, Any]
    ) -> bool:
        """Detect alternating A,B,A,B pattern in conversation + current tool call.

        Checks if the last 3+ assistant tool calls + the current one form
        an alternating pattern (A, B, A, ...). Full signature match (name+args).
        """
        current_sig = self._get_tc_signature(current_tc)

        # Collect last tool call signatures from conversation (chronological)
        recent_sigs: List[Tuple[str, str]] = []
        for msg in conv:
            if msg.get("role") == "assistant":
                for tc in (msg.get("tool_calls") or []):
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

    def _matches_last_tool_call(self, 
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
            current_canon = json.dumps(
                json.loads(current_args), sort_keys=True
            )
            return current_canon == last_args
        except json.JSONDecodeError:
            return current_args == last_args

    def _extract_tool_calls_from_response(self, response: Any
    ) -> List[Dict[str, Any]]:
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

    def _get_signatures_from_conv(
        self, conv: List[Dict[str, Any]]
    ) -> List[Tuple[str, str]]:
        """Extract all tool call signatures from conversation in chronological order."""
        sigs: List[Tuple[str, str]] = []
        for msg in conv:
            if msg.get("role") == "assistant":
                for tc in (msg.get("tool_calls") or []):
                    sigs.append(self._get_tc_signature(tc))
        return sigs

    def _count_consecutive_from_end(
        self, sigs: List[Tuple[str, str]], target_sig: Tuple[str, str]
    ) -> int:
        """Count consecutive tool calls from the END of sigs that match target_sig."""
        count = 0
        for sig in reversed(sigs):
            if sig == target_sig:
                count += 1
            else:
                break
        return count

    def _count_fuzzy_in_window(
        self, sigs: List[Tuple[str, str]], target_sig: Tuple[str, str], look_back: int
    ) -> int:
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
        if not tool_calls:
            return False, None, None

        prior_sigs = self._get_signatures_from_conv(conv)
        for tc in tool_calls:
            sig = self._get_tc_signature(tc)

            # Exact match: consecutive repeats
            max_r = self.config.max_repeats
            if max_r > 0:
                consecutive = self._count_consecutive_from_end(prior_sigs, sig)
                if consecutive >= max_r:
                    return True, tc, sig

            # Fuzzy: total matches in window
            fuzzy_max = self.config.fuzzy_max_repeats
            if fuzzy_max > 0:
                fuzzy_count = self._count_fuzzy_in_window(
                    prior_sigs, sig, self.config.fuzzy_look_back
                )
                if fuzzy_count >= fuzzy_max:
                    return True, tc, sig

            # AB loop detection
            if self.config.ab_loop_detection:
                if self._detect_ab_loop(conv, tc):
                    return True, tc, sig

        return False, None, None

    async def stream_retry(
        self,
        augmented: List[Dict[str, Any]],
        upstream_model: str,
        upstream_url: str,
    ) -> Dict[str, Any]:
        """Make a non-streaming HTTP retry with augmented messages.

        Returns the JSON response from upstream, or None on failure.
        Used by the streaming buffer to retry when a tool call loop is
        detected during streaming.
        """
        try:
            import asyncio as _asyncio
            retry_resp = await _asyncio.wait_for(
                self._make_http_retry(augmented, upstream_model, upstream_url),
                timeout=60.0,
            )
            return retry_resp or {}
        except _asyncio.TimeoutError:
            return None
        except Exception:
            return None

    async def process_response(
        self,
        response: Response,
        context: FilterExecutionContext,
    ) -> Response:
        """Handle both streaming and non-streaming detection."""
        # For streaming post-process, if the streaming buffer already handled
        # the check (tls_cleared flag), skip entirely.
        if context.metadata.get("tls_cleared"):
            log("INFO", "tls_skip_already_checked",
                req_id=self._resolve_req_id(context))
            return response

        # For streaming post-process, the response is a StreamingResponse
        # already containing accumulated tool_calls, content, etc.
        # We run the same detection logic as non-streaming.

        req_id = self._resolve_req_id(context)

        # Get conversation from context metadata or upstream payload
        conv = context.metadata.get("conversation_history", []) or []
        request_msgs = context.metadata.get("request_messages", []) or []
        if not conv:
            upstream_msgs = context.upstream_payload.get("messages", []) or []
            if upstream_msgs:
                conv = copy.deepcopy(upstream_msgs)
            elif request_msgs:
                conv = copy.deepcopy(request_msgs)

        # Build signature list once
        prior_sigs = self._get_signatures_from_conv(conv)

        # Extract tool calls from response
        tool_calls = self._extract_tool_calls_from_response(response)
        if not tool_calls:
            # No tool_calls in response — check if Phase 3 retry stored
            # tool_calls in context that we should inspect (streaming path).
            retry_tc = context.metadata.get("_retry_tool_calls")
            if retry_tc:
                is_loop, repeated_tc, last_tool = self.check_tool_calls_for_loop(retry_tc, conv)
                if is_loop and repeated_tc is not None and last_tool is not None:
                    log("INFO", "tls_retry_tc_suppressed",
                        req_id=req_id,
                        function_name=repeated_tc.get("function", {}).get("name", "?"))
                    result = self._handle_fallback(response, req_id, last_tool[0], context)
                    result._retry_tc_suppressed = True
                    return result
            return response  # No tool calls — nothing to check

        # Use shared detection method
        is_loop, repeated_tc, last_tool = self.check_tool_calls_for_loop(tool_calls, conv)
        if is_loop and repeated_tc is not None and last_tool is not None:
            self._log_filter_executed(req_id, ["model_tool_loop_stopper"],
                                      status="loop_detected")
            return await self._handle_loop_detected(
                response, context, conv, repeated_tc, req_id, last_tool
            )

        return response  # No loop detected

    async def _handle_loop_detected(self,
        response: Response,
        context: FilterExecutionContext,
        conv: List[Dict[str, Any]],
        repeated_tc: Dict[str, Any],
        req_id: str,
        last_tool: Tuple[str, str],
        is_fuzzy: bool = False,
    ) -> Response:
        """Inject TLS, retry with upstream."""
        tls_type = "fuzzy" if is_fuzzy else "exact"
        log(
            "INFO",
            "tool_loop_detected",
            req_id=req_id,
            function_name=repeated_tc.get("function", {}).get("name", "?"),
            repeated=True,
            tls_type=tls_type,
        )
        emit_tool_loop_detected(
            context,
            function_name=repeated_tc.get("function", {}).get("name", "?"),
            args_hash=repeated_tc.get("function", {}).get("arguments", "{}")[:64],
        )

        # Build augmented conversation: original + repeated assistant + TLS messages
        augmented = copy.deepcopy(conv)

        # Add the repeated tool call as assistant message
        augmented.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [repeated_tc],
            }
        )

        # Build stronger TLS messages: tool result + optional user message
        augmented = self._build_tls_messages(augmented, repeated_tc, req_id)

        log(
            "INFO",
            "tls_intervention",
            req_id=req_id,
            injected_tool_result=True,
            messages_count=len(augmented),
            tls_type=tls_type,
        )
        emit_tls_intervention(context, messages_count=len(augmented))

        return await self._tls_retry(augmented, response, context, req_id, repeated_tc, last_tool, is_fuzzy)

    def _build_tls_messages(
        self,
        augmented: List[Dict[str, Any]],
        repeated_tc: Dict[str, Any],
        req_id: str,
    ) -> List[Dict[str, Any]]:
        """Build stronger TLS messages: tool result + optional user instruction."""
        # Tool result (always added)
        tls_tool_msg = {
            "role": "tool",
            "tool_call_id": repeated_tc.get("id", "tls_intervention"),
            "content": self.config.tls_message,
        }
        augmented.append(tls_tool_msg)

        # User message (stronger signal — tells the model to STOP this function)
        if self.config.send_user_message:
            func = repeated_tc.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", "{}")
            try:
                args_preview = json.dumps(json.loads(args), separators=(",", ":"))
                if len(args_preview) > 100:
                    args_preview = args_preview[:100] + "..."
            except Exception:
                args_preview = args[:80]
            user_msg = {
                "role": "user",
                "content": (
                    f"SYSTEM: You just called `{name}()` with `{args_preview}`. "
                    f"This same function has already been called multiple times with different arguments. "
                    f"Do NOT call `{name}()` again. "
                    f"Proceed with a completely different tool or action."
                ),
            }
            augmented.append(user_msg)

            log("INFO", "tls_added_user_message",
                req_id=req_id,
                function_name=name,
                message=f"Do NOT call {name}() again")

        return augmented

    async def _tls_retry(
        self,
        augmented: List[Dict[str, Any]],
        response: Response,
        context: FilterExecutionContext,
        req_id: str,
        repeated_tc: Dict[str, Any],
        last_tool: Tuple[str, str],
        is_fuzzy: bool,
    ) -> Response:
        """Make upstream retry with augmented messages and handle result."""
        upstream_model = self._resolve_upstream_model(context)
        upstream_url = self._resolve_upstream_url(context)

        if not upstream_url:
            log("ERROR", "tls_missing_upstream_url", req_id=req_id)
            return response
        assert upstream_model is not None

        log(
            "INFO",
            "tls_retry",
            req_id=req_id,
            model=upstream_model,
            messages_count=len(augmented),
        )
        emit_tls_retry(context, model=str(upstream_model), messages_count=len(augmented))

        import asyncio as _asyncio

        try:
            retry_resp = await _asyncio.wait_for(
                self._make_http_retry(augmented, upstream_model, upstream_url),
                timeout=15.0,
            )
        except _asyncio.TimeoutError:
            log("WARNING", "tls_retry_timeout", req_id=req_id, timeout=15.0,
                function_name=last_tool[0])
            return self._handle_fallback(response, req_id, last_tool[0], context)
        except _asyncio.CancelledError:
            log("WARNING", "tls_retry_cancelled", req_id=req_id)
            raise  # propagate cancellation to caller

        retry_content, retry_tool_calls, retry_finish_reason, retry_reasoning = self._extract_retry_response(retry_resp)

        log(
            "INFO",
            "tls_response",
            req_id=req_id,
            content=retry_content[:200] if retry_content else "",
            length=len(retry_content),
        )

        # Check if model repeated the same tool call AGAIN (fallback)
        if retry_tool_calls:
            new_tcs = self._filter_repeated_tool_calls(retry_tool_calls, last_tool)
            if new_tcs:
                # Model tried a new tool — accept response, filter out repeated ones
                retry_tool_calls = new_tcs
            else:
                # All tool calls are repeats — fallback
                func_name = repeated_tc.get("function", {}).get("name", "?")
                return self._handle_fallback(response, req_id, func_name, context)

        # Build reconstructed response
        response_content = ""
        if hasattr(response, "content"):
            response_content = response.content

        final_content = (response_content or "") + "\n" + (retry_content or "")
        final_content = final_content.strip()

        self._log_filter_executed(req_id, ["model_tool_loop_stopper"], tls_intervened=True)

        kwargs: Dict[str, Any] = {"content": final_content}
        if retry_tool_calls:
            kwargs["tool_calls"] = retry_tool_calls
        # Terminal reason for the reconstructed response, not the retry fragment's
        # (a "length" here would surface a spurious truncation notice on the client).
        kwargs["finish_reason"] = "tool_calls" if retry_tool_calls else "stop"
        if retry_reasoning:
            kwargs["reasoning_content"] = retry_reasoning
        return type(response)(**kwargs)

    def _extract_retry_response(
        self, retry_resp: Any
    ) -> Tuple[str, List[Dict[str, Any]], Optional[str], str]:
        """Extract content, tool_calls, finish_reason, reasoning_content from retry response."""
        if retry_resp is None:
            return ("", [], None, "")
        retry_content = ""
        retry_tool_calls: List[Dict[str, Any]] = []
        retry_finish_reason = None
        retry_reasoning = ""
        try:
            choices = retry_resp.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                retry_content = msg.get("content", "") or ""
                retry_tool_calls = msg.get("tool_calls", [])
                retry_finish_reason = choices[0].get("finish_reason")
                retry_reasoning = msg.get("reasoning_content", "") or ""
        except (AttributeError, TypeError):
            pass

        # Detect XML <tool_call> in text content
        if not retry_tool_calls and retry_content and (
            "<tool_call>" in retry_content or "<function=" in retry_content
        ):
            from keeprollming.filters.nudge.request import _parse_xml_tool_call
            parsed = _parse_xml_tool_call(retry_content)
            if parsed:
                retry_tool_calls = [{
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": parsed["name"],
                        "arguments": json.dumps(parsed["arguments"]),
                    },
                }]
                tc_start = retry_content.find("<tool_call>")
                if tc_start >= 0:
                    retry_content = retry_content[:tc_start].rstrip()
                retry_finish_reason = "tool_calls"

        return retry_content, retry_tool_calls, retry_finish_reason, retry_reasoning

    def _filter_repeated_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
        last_tool: Optional[Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        """Remove repeated tool calls, keep new ones."""
        new_tcs = []
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", "{}")
            if not self._matches_last_tool_call(name, args, last_tool):
                new_tcs.append(tc)
        return new_tcs

    def _format_fallback_message(
        self, req_id: str, function_name: str, function_args: str = ""
    ) -> str:
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
        return StreamingResponse(content=fallback_content or "Stopped repeating tool call", model=response.model)

    def reset(self) -> None:
        """Reset any internal state between requests."""
        pass
