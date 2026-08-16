"""
Reasoning Loop Stopper (RLS) Filter - Detects and breaks LLM reasoning loops.

When a model emits the same reasoning_content block it just produced (identical text),
this filter intervenes by injecting a nudge message and retrying upstream.

Architecture:
1. Extract last reasoning_content from conversation history
2. Compare with reasoning_content in current model response
3. If identical → inject nudge message, call upstream again
4. Return reconstructed response with retry content
5. If model repeats again after RLS → return fallback message

Priority: 20 (runs BEFORE TLS at 25, after summarization at 15)

Streaming is handled by the stream finalizer pipeline.
"""

import asyncio
import copy
import json
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
from keeprollming.logger import log
from keeprollming.orchestrator.filters.events import (
    emit_reasoning_loop_detected,
    emit_rls_intervention,
    emit_rls_fallback,
)


# ── Module-level reasoning cache ─────────────────────────────────────────────
# Reasoning_content is NOT preserved in conversation history by clients/FE,
# so we cache it here to detect loops across requests.
_rls_cache: Dict[str, str] = {}
"""Cache of last reasoning_content seen, keyed by conversation prefix."""

def _rls_cache_key(conv: List[Dict[str, Any]]) -> str:
    """Build a stable cache key from conversation prefix.

    Uses the first user message text as key.  Imperfect but works for
    single-conversation scenarios.  Falls back to 'default' if no user
    message found.
    """
    for msg in conv:
        if msg.get("role") == "user":
            raw = msg.get("content") or ""
            # Content can be a list (multi-modal) or a string
            if isinstance(raw, list):
                text = " ".join(
                    p.get("text", "") for p in raw
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            elif isinstance(raw, str):
                text = raw
            else:
                text = str(raw)
            text = text.strip()
            if text:
                return text[:120]
    return "default"

def _rls_get_last(conv: List[Dict[str, Any]]) -> Optional[str]:
    """Get last reasoning: first from conv history, fall back to module cache."""
    # Try conversation history first
    for msg in reversed(conv):
        if msg.get("role") == "assistant":
            r = msg.get("reasoning_content")
            if r and isinstance(r, str):
                return r
    # Fall back to cache
    key = _rls_cache_key(conv)
    cached = _rls_cache.get(key)
    if cached:
        return cached
    return None

def _rls_set_cache(conv: List[Dict[str, Any]], reasoning: str) -> None:
    """Update module cache with the latest reasoning seen."""
    key = _rls_cache_key(conv)
    _rls_cache[key] = reasoning
    # Keep cache bounded
    if len(_rls_cache) > 100:
        # Remove oldest entries
        for k in list(_rls_cache.keys())[:-50]:
            del _rls_cache[k]


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class ReasoningLoopStopperConfig(FilterConfig):
    """Configuration for the RLS filter."""

    max_retries: int = 2
    retry_timeout: int = 120

    max_repeats: int = 1
    """Number of consecutive identical reasoning blocks that trigger detection.
    0 = disabled. 1 = fire on first repeat."""

    rls_message: str = (
        "You have already used this exact reasoning. Please proceed with "
        "a different approach or stop thinking about it."
    )
    fallback_message: str = (
        "I notice I'm repeating the same reasoning. Let me try a different approach."
    )

    fallback_streaming_message: str = (
        "I seem to be thinking in circles. I will pause and reconsider."
    )

    send_user_message: bool = True
    """If True, inject a user role message in addition to the RLS message."""

    def __post_init__(self):
        super().__post_init__()
        self.name = "reasoning_loop_stopper"


class ReasoningLoopStopperFilter(Filter):
    """Detects and breaks LLM reasoning loops by comparing reasoning_content.

    The RLS finalizer owns streaming loop intervention.
    """

    priority = 20

    # ── Init ──────────────────────────────────────────────────────────

    def __init__(self, config):
        if isinstance(config, dict):
            enabled = config.get("enabled", False)
        else:
            enabled = bool(config)

        filter_config = ReasoningLoopStopperConfig(
            enabled=enabled,
            max_repeats=config.get("max_repeats", 1) if isinstance(config, dict) else 1,
            rls_message=config.get("rls_message",
                "You have already used this exact reasoning. Please proceed with a different approach or stop thinking about it.") if isinstance(config, dict) else "You have already used this exact reasoning. Please proceed with a different approach or stop thinking about it.",
            fallback_message=config.get("fallback_message",
                "I notice I'm repeating the same reasoning. Let me try a different approach.") if isinstance(config, dict) else "I notice I'm repeating the same reasoning. Let me try a different approach.",
            max_retries=config.get("max_retries", 2) if isinstance(config, dict) else 2,
            fallback_streaming_message=config.get("fallback_streaming_message",
                "I seem to be thinking in circles. I will pause and reconsider.") if isinstance(config, dict) else "I seem to be thinking in circles. I will pause and reconsider.",
            send_user_message=config.get("send_user_message", True) if isinstance(config, dict) else True,
        )
        super().__init__(filter_config)
        self._upstream_url = config.get("upstream_url", "") if isinstance(config, dict) else ""

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_last_reasoning(self, conv: List[Dict[str, Any]]) -> Optional[str]:
        """Extract reasoning_content from conv history, falling back to module cache."""
        return _rls_get_last(conv)

    def _get_last_tool_call(self, conv: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Get the LAST assistant tool_call from conversation history."""
        for msg in reversed(conv):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tcs = msg["tool_calls"]
                if isinstance(tcs, list) and tcs:
                    return tcs[0]
        return None

    def _get_current_tool_call(self, combined: bytes) -> Optional[Dict[str, Any]]:
        """Extract tool call from the buffered SSE chunks."""
        text = combined.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                try:
                    d = json.loads(line[6:].strip())
                    choices = d.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        if "tool_calls" in delta and delta["tool_calls"]:
                            return delta["tool_calls"][0]
                except Exception:
                    pass
        return None

    def _update_cache(self, conv: List[Dict[str, Any]], reasoning: str) -> None:
        """Store current reasoning in module cache."""
        _rls_set_cache(conv, reasoning)

    def _get_reasoning_from_response(self, response: Any) -> str:
        """Extract reasoning_content from response object."""
        if hasattr(response, "reasoning_content"):
            return response.reasoning_content or ""
        return ""

    async def stream_retry(
        self,
        augmented: List[Dict[str, Any]],
        upstream_model: str,
        upstream_url: str,
    ) -> Dict[str, Any]:
        """Make a non-streaming HTTP retry with augmented messages.

        Returns the JSON response from upstream, or None on failure.
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

    # ── Request phase ─────────────────────────────────────────────────

    async def process_request(self, request: Request, context: FilterExecutionContext) -> Request:
        """Pass through — RLS only modifies responses."""
        return request

    # ── Response phase (non-streaming) ────────────────────────────────

    async def process_response(
        self,
        response: Response,
        context: FilterExecutionContext,
    ) -> Response:
        """Handle both streaming and non-streaming detection."""
        # Skip if streaming buffer already handled it
        if context.metadata.get("rls_cleared"):
            log("INFO", "rls_skip_already_checked",
                req_id=self._resolve_req_id(context))
            return response

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

        # Extract reasoning from response
        current_reasoning = self._get_reasoning_from_response(response)
        if not current_reasoning:
            return response  # No reasoning — nothing to check

        log("INFO", "rls_reasoning_found",
            req_id=req_id,
            reasoning=current_reasoning[:500],
            reasoning_length=len(current_reasoning))

        # Get last reasoning from conversation history (falls back to module cache)
        last_reasoning = self._get_last_reasoning(conv)
        if last_reasoning is None:
            # First reasoning seen — cache it for next time
            log("INFO", "rls_first_reasoning_cached",
                req_id=req_id,
                reasoning=current_reasoning[:200])
            self._update_cache(conv, current_reasoning)
            return response

        log("INFO", "rls_comparing_reasoning",
            req_id=req_id,
            last_reasoning=last_reasoning[:200],
            current_reasoning=current_reasoning[:200],
            match=current_reasoning.strip() == last_reasoning.strip())

        # Check for exact match (loop)
        max_r = self.config.max_repeats
        if max_r > 0 and current_reasoning.strip() == last_reasoning.strip():
            # Rich comparison: if tool calls differ, model is doing different
            # things — not a loop despite identical reasoning.
            _rls_is_loop = True
            _curr_tcs = getattr(response, 'tool_calls', None)
            if _curr_tcs:
                _curr_names = set()
                for _tc in _curr_tcs:
                    _fn = _tc.get("function", {})
                    _n = _fn.get("name", "")
                    if _n:
                        _curr_names.add(_n)
                if _curr_names:
                    _prev_names = set()
                    for _m in reversed(conv):
                        if _m.get("role") == "assistant":
                            for _t in (_m.get("tool_calls") or []):
                                _n2 = (_t.get("function") or {}).get("name", "")
                                if _n2:
                                    _prev_names.add(_n2)
                            break
                    if _prev_names and _curr_names != _prev_names:
                        _rls_is_loop = False
            if _rls_is_loop:
                self._log_filter_executed(req_id, ["reasoning_loop_stopper"],
                                          status="loop_detected")
                return await self._handle_loop_detected(
                    response, context, conv, req_id, current_reasoning
                )
            else:
                self._update_cache(conv, current_reasoning)
                return response

        # Different reasoning — update cache for next check
        self._update_cache(conv, current_reasoning)
        log("INFO", "rls_reasoning_different_passed",
            req_id=req_id,
            reasoning=current_reasoning[:200])
        return response  # No loop detected

    async def _handle_loop_detected(
        self,
        response: Response,
        context: FilterExecutionContext,
        conv: List[Dict[str, Any]],
        req_id: str,
        reasoning: str,
    ) -> Response:
        """Inject RLS tool result, retry with upstream."""
        log("INFO", "reasoning_loop_detected",
            req_id=req_id, reasoning=reasoning[:200],
            max_repeats=self.config.max_repeats)
        emit_reasoning_loop_detected(context, reasoning=reasoning[:200])

        # Build augmented conversation: original + RLS message
        augmented = copy.deepcopy(conv)

        # Add RLS nudge as user message
        augmented.append({
            "role": "user",
            "content": (
                f"SYSTEM: You just repeated the same reasoning verbatim. "
                f"Please stop thinking about this and proceed with a completely "
                f"different approach or report what you already know."
            ),
        })

        log("INFO", "rls_intervention", req_id=req_id,
            messages_count=len(augmented))
        emit_rls_intervention(context, messages_count=len(augmented))

        return await self._rls_retry(augmented, response, context, req_id, reasoning)

    async def _rls_retry(
        self,
        augmented: List[Dict[str, Any]],
        response: Response,
        context: FilterExecutionContext,
        req_id: str,
        reasoning: str,
    ) -> Response:
        """Make upstream retry with augmented messages and handle result."""
        upstream_model = self._resolve_upstream_model(context)
        upstream_url = self._resolve_upstream_url(context)

        if not upstream_url:
            log("ERROR", "rls_missing_upstream_url", req_id=req_id)
            return response
        assert upstream_model is not None

        log("INFO", "rls_retry", req_id=req_id,
            model=upstream_model, messages_count=len(augmented))

        import asyncio as _asyncio
        try:
            retry_resp = await _asyncio.wait_for(
                self._make_http_retry(augmented, upstream_model, upstream_url),
                timeout=15.0,
            )
        except _asyncio.TimeoutError:
            log("WARNING", "rls_retry_timeout", req_id=req_id)
            return self._handle_fallback(response, req_id, context)
        except _asyncio.CancelledError:
            log("WARNING", "rls_retry_cancelled", req_id=req_id)
            raise

        if retry_resp is None:
            log("WARNING", "rls_retry_failed", req_id=req_id)
            return self._handle_fallback(response, req_id, context)

        choices = retry_resp.get("choices", [])
        if not choices:
            return self._handle_fallback(response, req_id, context)

        msg = choices[0].get("message", {})
        retry_content = msg.get("content", "") or ""
        retry_tc = msg.get("tool_calls", [])
        retry_finish_reason = choices[0].get("finish_reason")
        retry_reasoning = msg.get("reasoning_content", "") or ""

        log("INFO", "rls_response", req_id=req_id,
            content=retry_content[:200] if retry_content else "",
            length=len(retry_content))

        # Build reconstructed response
        response_content = ""
        if hasattr(response, "content"):
            response_content = response.content

        final_content = (response_content or "") + "\n" + (retry_content or "")
        final_content = final_content.strip()

        kwargs: Dict[str, Any] = {"content": final_content}
        kwargs["model"] = getattr(response, "model", "")
        if retry_tc:
            kwargs["tool_calls"] = retry_tc
        if retry_reasoning:
            kwargs["reasoning_content"] = retry_reasoning
        # Terminal reason for the reconstructed response, not the retry fragment's
        # (a "length" here would surface a spurious truncation notice on the client).
        kwargs["finish_reason"] = "tool_calls" if retry_tc else "stop"
        return type(response)(**kwargs)

    def _handle_fallback(self, response: Response, req_id: str, context) -> Response:
        """Model repeated reasoning after RLS intervention — return fallback."""
        fallback = self.config.fallback_message or "Stopped repeating reasoning"
        log("INFO", "rls_fallback", req_id=req_id)
        emit_rls_fallback(context)
        self._log_filter_executed(req_id, ["reasoning_loop_stopper"], status="fallback")
        return StreamingResponse(
            content=fallback,
            model=response.model,
        )

    def reset(self) -> None:
        """Reset any internal state between requests."""
        pass
