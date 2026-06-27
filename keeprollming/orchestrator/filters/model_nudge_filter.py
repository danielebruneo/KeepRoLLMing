"""
Model Nudge Filter - Detects and handles lazy LLM responses.

This filter intercepts LLM responses that end with patterns indicating
the model was about to do something (tool call, plan, etc.) but stopped
prematurely. It implements a COMPLETE retry cycle internally:

1. Detect lazy pattern in response
2. Add nudge message to conversation
3. Make HTTP retry request to upstream model
4. Concatenate responses with newline separator
5. Check if still lazy - repeat or exit
6. Return final concatenated response

Example lazy responses this filter handles:
  - "Now I will:"
  - "Here's the plan:"
  - "I'll do this:"
  - Any text ending with ":"

Streaming: Inherits from StreamingFilterBase for buffer/retry/keepalive management.
"""

import copy
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import inspect
import httpx

from ..filter import (
    FilterConfig,
    FilterExecutionContext,
    Request,
    Response,
    RetryDecision,
    StreamChunkResult,
    register_filter,
)
from .streaming_filter import StreamingFilterBase, StreamingFilterConfig
from keeprollming.logging import get_filter_logger
from keeprollming.logger import log


def _parse_xml_tool_call(text: str) -> dict | None:
    """Parse XML <tool_call> text into structured tool_calls format.
    
    Expected format:
    <tool_call>
    <function=run_shell_command>
    <parameter=command>ssh -o ...</parameter>
    <parameter=description>Check something</parameter>
    </function>
    </tool_call>
    
    Returns: {"name": "...", "arguments": {...}} or None if parsing fails.
    """
    if "<tool_call>" not in text and "<function=" not in text:
        return None
    
    try:
        # Extract function name: <function=xxx>
        fn_match = re.search(r'<function=([^>]+)>', text)
        if not fn_match:
            return None
        fn_name = fn_match.group(1)
        
        # Extract parameters: <parameter=key>value</parameter>
        args = {}
        for pm in re.finditer(r'<parameter=(\S+)>(.*?)</parameter>', text, re.DOTALL):
            key = pm.group(1)
            value = pm.group(2).strip()
            args[key] = value
        
        return {
            "name": fn_name,
            "arguments": args,
        }
    except Exception:
        return None


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class ModelNudgeConfig(StreamingFilterConfig):
    """Configuration for the Model Nudge filter."""

    trigger_patterns: List[str] = field(default_factory=lambda: [":$"])
    """Regex patterns matching end of lazy responses."""

    action: str = "nudge"
    """Action to take: 'nudge' or 'regenerate'."""

    nudge_message: str = "Continue."
    """Message to inject when action='nudge'."""

    max_nudge_attempts: int = 3
    """Maximum consecutive nudges before giving up."""

    nudge_on_empty: bool = False
    """Retry on empty upstream responses."""

    nudge_fallback_message: str = ""
    """Fallback message when max attempts reached."""

    def __post_init__(self):
        super().__post_init__()
        self.name = "model_nudge"


def _parse_sse_response(raw: str) -> dict | None:
    """Parse SSE stream into response dict (content + tool_calls).
    Handles both SSE format (data: {...}\n\n) and raw JSON (non-streaming response).
    """
    # Try raw JSON first (non-streaming upstream responses like when stream=False)
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            d = json.loads(stripped)
            choices = d.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                return {
                    "choices": [{
                        "message": {
                            "content": msg.get("content", ""),
                            "tool_calls": msg.get("tool_calls"),
                        },
                        "finish_reason": choices[0].get("finish_reason", "stop"),
                    }]
                }
        except Exception:
            pass

    # Fallback: SSE format (data: {...}\n\n)
    content_parts = []
    tool_calls = []
    finish_reason = None
    for line in raw.split(chr(10)):
        if line.startswith("data: ") and "[DONE]" not in line:
            try:
                d = json.loads(line[6:].strip())
                for c in d.get("choices", []):
                    delta = c.get("delta", {})
                    if "content" in delta and delta["content"]:
                        content_parts.append(delta["content"])
                    if "tool_calls" in delta:
                        tool_calls.extend(delta["tool_calls"])
                    if c.get("finish_reason"):
                        finish_reason = c.get("finish_reason")
            except Exception:
                pass
    if not content_parts and not tool_calls:
        return None
    return {
        "choices": [{
            "message": {
                "content": "".join(content_parts),
                "tool_calls": tool_calls if tool_calls else None,
            },
            "finish_reason": finish_reason or "stop",
        }]
    }


@register_filter("model_nudge")
class ModelNudgeFilter(StreamingFilterBase):
    """
    Filter that detects lazy LLM responses and handles retry internally.

    Configuration:
        enabled (bool): Enable/disable filter (default: True)
        trigger_patterns (List[str]): Regex patterns matching end of lazy responses
        action (str): "nudge" or "regenerate" (default: "nudge")
        nudge_message (str): Message to inject when action="nudge" (default: "Continue.")
        max_nudge_attempts (int): Maximum consecutive nudges before giving up (default: 3)

    Example config:
        model_nudge:
          enabled: true
          trigger_patterns: [":$", "\\bnow I will\\b", "\\bhere's the plan\\b"]
          action: nudge
          nudge_message: "Continue, you need to produce a tool call."
          max_nudge_attempts: 3

    Usage in route config:
        routes:
          - name: my_route
            model: anthropic/claude-3
            filter_chain:
              order: [model_nudge]
              filters:
                model_nudge:
                  enabled: true
                  trigger_patterns: [":$", "\\bnow I will\\b"]
                  action: nudge
                  nudge_message: "Continue with the tool call."
    """

    priority = 50

    def __init__(self, config):
        """
        Initialize ModelNudgeFilter.

        Args:
            config: Can be ModelNudgeConfig object or dict with configuration values
        """
        if isinstance(config, dict):
            filter_config = ModelNudgeConfig(
                enabled=config.get("enabled", True),
                trigger_patterns=config.get("trigger_patterns", [":$"]),
                action=config.get("action", "nudge"),
                nudge_message=config.get("nudge_message", "Continue."),
                max_nudge_attempts=config.get("max_nudge_attempts", 3),
                nudge_on_empty=config.get("nudge_on_empty", False),
                nudge_fallback_message=config.get("nudge_fallback_message", ""),
                max_retries=config.get("max_retries", 2),
            )
        elif isinstance(config, ModelNudgeConfig):
            filter_config = config
        else:
            filter_config = ModelNudgeConfig(enabled=True)

        super().__init__(filter_config)
        self._upstream_url = config.get("upstream_url", "") if isinstance(config, dict) else ""

        # Re-enable re-stream during nudge buffering so the client sees
        # content live.  Phase 4 will skip its duplicate content yield
        # when it detects that buffer chunks were already forwarded.
        # buffering the content keeps the client from seeing stale timestamps
        # or lazy responses before the nudge decides.
        self._emit_while_buffering = False

        # Initialize logger for this filter
        self._logger = get_filter_logger("model_nudge")

        # Compile trigger patterns
        self._init_patterns(self.config.trigger_patterns)

    def _init_patterns(self, patterns: List[str]) -> None:
        """Compile regex patterns for matching."""
        self._trigger_patterns = []
        for pattern in patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._trigger_patterns.append(compiled)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}")

    @staticmethod
    def _strip_timestamp_footer(content: str) -> str:
        """Remove the TimestampFilter footer appended by a previous turn.

        The TimestampFilter appends ``\\n\\n---\\nTimestamp: <datetime> UTC``
        to the response content.  When the LLM echoes back conversation history,
        this stale footer can appear inside the current assistant response,
        masking lazy ``:`` patterns from the nudge filter.

        Returns content with the stale footer removed (if found).
        """
        import re as _re

        # Match optional newlines before ---\nTimestamp: ... (with optional brackets)
        pattern = _re.compile(
            r"\n*---\n\[?Timestamp: .+?(?: UTC)?$",
            _re.DOTALL,
        )
        stripped = pattern.sub("", content)
        # Also re-strip trailing newlines/whitespace the model may have left
        return stripped.rstrip()

    def _matches_lazy_response(self, content: str) -> bool:
        """
        Check if response matches lazy patterns.
        
        Patterns ending with $ match at END of string (suffix matching).
        Other patterns can match anywhere in the text.
        Uses \\Z for true end-of-string anchor to handle Unicode/emoji correctly.
        """
        if not self._trigger_patterns:
            return False

        for pattern in self._trigger_patterns:
            # Handle $ anchor specially to support Unicode/emoji correctly
            if pattern.pattern.endswith('$'):
                # Remove trailing $ and check manually
                base_pattern = pattern.pattern[:-1]
                
                # Special case: just ":", do simple string check (case insensitive)
                if base_pattern == ":":
                    stripped = content.rstrip()
                    if len(stripped) > 0 and stripped[-1].lower() == ':':
                        return True
                else:
                    # Other patterns ending with $ - use regex with \Z anchor
                    regex_with_z = base_pattern + r"\Z"
                    match = re.search(regex_with_z, content, pattern.flags)
                    if match:
                        return True
            else:
                # Regular pattern (no $ anchor) - can match anywhere
                match = pattern.search(content)
                if match:
                    return True

        return False

    async def process_request(
        self,
        request: Request,
        context: FilterExecutionContext
    ) -> Request:
        """No-op for ModelNudgeFilter - only processes responses."""
        # Store context for use in HTTP retry calls
        self._context = context
        return request

    async def process_response(
        self,
        response: Response,
        context: FilterExecutionContext
    ) -> Response:
        """
        Process response and detect lazy behavior with internal retry loop.
        
        This filter implements the COMPLETE nudge retry cycle internally:
        1. Check if response matches lazy pattern
        2. If not lazy: return as-is
        3. If lazy and max_attempts > 0:
           a. Add nudge message to conversation
           b. Make HTTP retry request
           c. Remove nudge message from conversation
           d. Concatenate responses with newline separator
           e. Check if still lazy - repeat or exit
        4. Return final concatenated response
        
        Args:
            response: LLM response to check
            context: Execution context for state management
            
        Returns:
            Response object with final concatenated content (or original if not lazy)
        """
        self._context = context
        req_id = self._resolve_req_id(context)
        context.req_id = req_id  # propagate to _make_http_retry for consistent logging

        # Skip if not enabled
        if not self.is_enabled:
            return response

        content = response.content or ""

        # Strip any stale timestamp footer that may have been echoed back
        # from conversation history by the LLM.  The TimestampFilter will
        # re-add a fresh timestamp after nudge processing completes.
        if content:
            stripped = self._strip_timestamp_footer(content)
            if stripped != content:
                log("DEBUG", "nudge_stripped_stale_timestamp",
                    req_id=req_id, original_len=len(content), stripped_len=len(stripped))
                content = stripped

        # ── Empty response handling ──
        # When upstream returns a 200 OK with empty body (no tokens), retry directly
        # without regex matching. Tool calls in flight are NOT empty — skip those.
        _empty_nudge = False
        if self.config.nudge_on_empty and not content.strip():
            _has_tc = hasattr(response, "tool_calls") and bool(response.tool_calls)
            if not _has_tc:
                log("INFO", "nudge_response_empty", req_id=req_id,
                    nudge_on_empty=True, content="[empty]", max_attempts=self.config.max_nudge_attempts)
                _empty_nudge = True

        # ── Tool call embedded in content (XML) — skip lazy detection
        # ToolRewriteFilter may have already cleaned XML and set tool_calls.
        # If so, don't re-trigger lazy even if cleaned content ends with ':'.
        if context.metadata.get("tool_rewrite_done") and hasattr(response, "tool_calls") and response.tool_calls:
            log("INFO", "nudge_skip_rewritten_tc",
                req_id=req_id, content_length=len(content))
            return response

        # No lazy pattern detected - return as-is (unless _empty_nudge)
        if not _empty_nudge and not self._matches_lazy_response(content):
            log(
                "INFO",
                "nudge_response_valid",
                req_id=req_id,
                response_content=content[:100],
            )
            return response

        # If response has tool_calls, the model IS producing an action.
        # Skip nudge entirely — the content's trailing ':' is not lazy, it's
        # just the end of the text before the tool call.
        if hasattr(response, "tool_calls") and response.tool_calls:
            log(
                "INFO",
                "nudge_skip_has_tool_calls",
                req_id=req_id,
                response_content=content[:100],
                tool_calls_count=len(response.tool_calls),
            )
            return response

        # Also skip if a tool call is embedded in the content (XML <tool_call> or
        # structured JSON) — the model is taking action even if the text ends with ":".
        if _parse_xml_tool_call(content) is not None:
            log(
                "INFO",
                "nudge_skipped_embedded_tc",
                req_id=req_id,
                response_content=content[:100],
            )
            return response

        # Lazy pattern detected - start retry cycle
        accumulator = content  # Start with first (lazy) response
        last_tool_calls = []   # Track tool_calls from last retry (for streaming)
        last_finish_reason = None  # Track finish_reason from retry response
        last_reasoning_content = ""  # Track reasoning_content from retry response
        
        # Deep copy conversation history for modifications during retries
        # Read messages from upstream_payload (the request payload)
        conversation_history = copy.deepcopy(context.upstream_payload.get("messages") or [])

        # Append the lazy assistant response so the model knows what to continue from
        conversation_history.append({
            "role": "assistant",
            "content": content
        })
        
        max_attempts = self.config.max_nudge_attempts
        # Use resolved upstream model for retry (not the original route model which the upstream doesn't know)
        model = context.upstream_model or context.upstream_payload.get('_original_model') or context.upstream_payload.get('model', 'unknown') if context.upstream_payload else 'unknown'
        
        # Log retry start context in clean key=value format
        log(
            "INFO",
            "nudge_retry_start",
            req_id=req_id,
            pattern=":$",
            max_attempts=max_attempts,
            content_length=len(accumulator),
        )
        
        log(
            "INFO",
            "assistant_lazy_response",
            req_id=req_id,
            content=accumulator[:500],
            total_length=len(accumulator),
        )
        
        self._logger.nudge_triggered(
            trigger_pattern=":$",
            response_content=accumulator[:100],
            nudge_attempt=1,
            action=self.config.action,
            max_attempts=max_attempts,
        )

        # Retry loop - attempt up to max_attempts times
        for attempt in range(max_attempts):
            log(
                "INFO",
                "USER_NUDGE",
                req_id=req_id,
                message=self.config.nudge_message,
                attempt=attempt + 1,
                max_attempts=max_attempts,
            )
            
            # Add nudge message to conversation for retry
            conversation_history.append({
                "role": "user", 
                "content": self.config.nudge_message
            })
            


            # Use Pipeline's upstream caller if available (V2), otherwise direct HTTP (V1)
            if context._upstream_caller:
                # V2 Pipeline: upstream_caller may be an async generator (SSE chunks)
                # or a regular async function (returns response JSON)
                retry_payload = dict(context.upstream_payload)
                retry_payload["messages"] = conversation_history
                # Use streaming retry only if the caller is an async generator
                # (streaming post-processing in Phase 4). For regular async functions
                # (non-streaming retry), keep the original stream flag.
                is_streaming_caller = inspect.isasyncgenfunction(context._upstream_caller)
                retry_payload["stream"] = is_streaming_caller
                try:
                    result = context._upstream_caller(retry_payload)
                    if inspect.isasyncgen(result):
                        # Async generator — collect SSE chunks and parse
                        chunks = []
                        async for chunk in result:
                            chunks.append(chunk)
                        raw = b"".join(chunks).decode("utf-8", errors="replace")
                        log("DEBUG", "nudge_v2_sse_raw",
                            req_id=req_id,
                            raw_len=len(raw),
                            raw_start=raw[:300])
                        retry_response = _parse_sse_response(raw)
                    else:
                        # Regular async function
                        retry_response = await result
                except TypeError:
                    # Fallback: upstream_caller is an unexpected type
                    log("WARN", "nudge_v2_caller_type_error", req_id=req_id)
                    retry_response = None
            else:
                # V1 FilterChain: direct HTTP call (legacy)
                effective_upstream_url = self._resolve_upstream_url(context)
                if not effective_upstream_url:
                    log(
                        "WARN",
                        "nudge_no_upstream_url_configured",
                        req_id=req_id,
                    )
                    break
                retry_response = await self._make_http_retry(
                    messages=conversation_history,
                    model=model,
                    upstream_url=effective_upstream_url,
                    original_payload=context.upstream_payload,
                )
            if retry_response is None:
                retry_content = ""
                retry_tool_calls = []
                retry_finish_reason = None
            else:
                msg = retry_response.get("choices", [{}])[0].get("message", {})
                retry_content = msg.get("content", "") or ""
                retry_tool_calls = msg.get("tool_calls", [])
                retry_finish_reason = retry_response.get("choices", [{}])[0].get("finish_reason")
                retry_reasoning = msg.get("reasoning_content", "") or ""
                if retry_tool_calls:
                    last_tool_calls = retry_tool_calls
                if retry_finish_reason:
                    last_finish_reason = retry_finish_reason
                if retry_reasoning:
                    last_reasoning_content = retry_reasoning
                # Parse XML tool_call text into structured tool_calls
                # Only if: TC is at the end of content AND not inside code block
                if not last_tool_calls and retry_content and (
                    "<tool_call>" in retry_content or "<function=" in retry_content
                ):
                    tc_start = retry_content.find("<tool_call>")
                    if tc_start >= 0:
                        tc_end_tag = retry_content.find("</tool_call>")
                        tc_end = (tc_end_tag + len("</tool_call>")) if tc_end_tag >= 0 else len(retry_content)
                        after_tc = retry_content[tc_end:].strip()
                        # Check if TC is inside triple backticks
                        prefix = retry_content[max(0, tc_start - 30):tc_start]
                        in_code_block = "```" in prefix
                        # Only parse if TC is at end and not in code block
                        if not after_tc and not in_code_block:
                            parsed = _parse_xml_tool_call(retry_content)
                            if parsed:
                                last_tool_calls = [{
                                    "id": f"call_{uuid.uuid4().hex[:8]}",
                                    "type": "function",
                                    "function": {
                                        "name": parsed["name"],
                                        "arguments": json.dumps(parsed["arguments"]),
                                    },
                                }]
                                # Remove tool_call text from content
                                retry_content = retry_content[:tc_start].rstrip()
                                if last_tool_calls:
                                    retry_tool_calls = last_tool_calls
                if retry_finish_reason:
                    last_finish_reason = retry_finish_reason
            
            # Remove nudge message from conversation (don't pass to next iteration)
            if conversation_history and conversation_history[-1]["role"] == "user":
                conversation_history.pop()

            # Add retry's assistant response to conversation so the model knows what it said
            conv_entry = {"role": "assistant", "content": retry_content}
            if retry_tool_calls:
                conv_entry["tool_calls"] = retry_tool_calls
            conversation_history.append(conv_entry)
            


            # Handle retry failure (no content AND no tool_calls)
            if not retry_content and not retry_tool_calls:
                log(
                    "WARN",
                    "nudge_retry_failed_giving_up",
                    req_id=req_id,
                    attempt=attempt + 1,
                )
                break
            
            # Log intermediate response during retry loop
            log(
                "INFO",
                "assistant_nudged_response",
                req_id=req_id,
                content=retry_content[:200],
                attempts=attempt + 2,  # +2 because we're in second iteration
            )
            
            # Check if retry response is still lazy
            if not self._matches_lazy_response(retry_content):
                # Not lazy anymore! Concatenate and return
                accumulator += "\n" + retry_content
                
                log(
                    "INFO", 
                    "assistant_final_response",
                    req_id=req_id,
                    content=accumulator[:500],
                    total_length=len(accumulator),
                    nudge_attempts=attempt + 2,
                )
                
                # Update context metadata for accurate tracking
                context.metadata["nudge_attempts"] = attempt + 2
                log(
                    "INFO",
                    "filter_chain_executed",
                    req_id=req_id,
                    filters=["model_nudge"],
                    status="complete",
                    nudge_attempts=attempt + 2,
                )
                
                # Return Response object with final concatenated content
                # Log the final accumulated content before returning (so it's inside the REQUEST block)
                log(

            "INFO",
            "assistant",
            req_id=req_id,
            content=accumulator[:500],
            total_length=len(accumulator),
        )

                kwargs = {"content": accumulator}
                if last_tool_calls:
                    kwargs["tool_calls"] = last_tool_calls
                # Report a terminal reason for the accumulated whole — NOT the
                # underlying retry fragment's finish_reason (which is often "length"
                # because the retry reused the clamped max_tokens, and would make the
                # client show a spurious "truncated due to token limits" message).
                kwargs["finish_reason"] = "tool_calls" if last_tool_calls else "stop"
                if last_reasoning_content:
                    kwargs["reasoning_content"] = last_reasoning_content
                return type(response)(**kwargs)


            else:
                # Still lazy! Concatenate and continue loop
                accumulator += "\n" + retry_content

                log(
                    "INFO",
                    "nudge_still_lazy",
                    req_id=req_id,
                    attempt=attempt + 2,
                    max_attempts=max_attempts,
                )

        # Max attempts reached or error occurred - return accumulated content
        log(
            "WARN",
            "nudge_max_attempts_reached",
            req_id=req_id,
            final_response_content=accumulator[:500],
            total_length=len(accumulator),
            max_attempts=max_attempts,
        )

        # Log the final accumulated content BEFORE filter_chain_executed
        log(
            "INFO",
            "assistant",
            req_id=req_id,
            content=accumulator[:500],
            total_length=len(accumulator),
        )

        # Log filter execution even when giving up
        log(
            "INFO",
            "filter_chain_executed",
            req_id=req_id,
            filters=["model_nudge"],
            status="gave_up_max_attempts",
            nudge_attempts=max_attempts,
        )
        kwargs = {"content": accumulator}
        if self.config.nudge_fallback_message:
            kwargs["content"] = accumulator + "\n" + self.config.nudge_fallback_message
        if last_tool_calls:
            kwargs["tool_calls"] = last_tool_calls
        # Orchestrator deliberately ends the turn here (gave up after max attempts).
        # Don't propagate the fragment's "length" — the accumulated response is what
        # the client gets; "length" would only surface a misleading truncation notice.
        kwargs["finish_reason"] = "tool_calls" if last_tool_calls else "stop"
        if last_reasoning_content:
            kwargs["reasoning_content"] = last_reasoning_content
        return type(response)(**kwargs)

    # ── StreamingFilterBase abstract methods ─────────────────────────────

    async def process_stream_chunk(
        self,
        chunk: bytes,
        context: FilterExecutionContext,
    ) -> StreamChunkResult:
        """Process streaming chunk with lazy response detection.

        Delegates to base class which handles buffering; _handle_intervention
        will check for lazy patterns when buffer flushes.

        Reasoning chunks (``reasoning_content`` without ``content``) are
        forwarded live even during buffering so the client sees thinking
        output in real time.

        Tool_call delta chunks are captured for Phase 4 merging (bypassing
        live emission) so the client receives a single merged tool_call with
        a separate finish_reason, instead of raw fragments.
        """
        if self._buffering and self._is_reasoning_chunk(chunk):
            return StreamChunkResult(emit=[chunk])
        return await super().process_stream_chunk(chunk, context)

    @staticmethod
    def _chunk_has_tool_calls(chunk: bytes) -> bool:
        """Check if an SSE chunk contains ``delta.tool_calls``."""
        try:
            txt = chunk.decode("utf-8")
            if not txt.startswith("data: ") or "[DONE]" in txt:
                return False
            payload = txt[6:].strip()
            if not payload:
                return False
            import json as _json
            obj = _json.loads(payload)
            for choice in obj.get("choices", []):
                delta = choice.get("delta", {})
                if isinstance(delta, dict) and delta.get("tool_calls"):
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    def _chunk_has_content(chunk: bytes) -> bool:
        """Check if an SSE chunk carries ``delta.content`` (non-empty string)."""
        try:
            txt = chunk.decode("utf-8")
            if not txt.startswith("data: ") or "[DONE]" in txt:
                return False
            payload = txt[6:].strip()
            if not payload:
                return False
            import json as _json
            obj = _json.loads(payload)
            for choice in obj.get("choices", []):
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    continue
                c = delta.get("content")
                if isinstance(c, str) and len(c) > 0:
                    return True
            return False
        except Exception:
            return False

    @staticmethod
    def _is_reasoning_chunk(chunk: bytes) -> bool:
        """Check if an SSE chunk carries only ``reasoning_content`` (no ``content``)."""
        try:
            txt = chunk.decode("utf-8")
            if not txt.startswith("data: "):
                return False
            payload = txt[6:].strip()
            if not payload or payload == "[DONE]":
                return False
            import json as _json
            obj = _json.loads(payload)
            for choice in obj.get("choices", []):
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    continue
                has_rc = "reasoning_content" in delta
                has_content = isinstance(delta.get("content"), str) and len(delta.get("content", "")) > 0
                return has_rc and not has_content
            return False
        except Exception:
            return False

    def _should_start_buffering(self, chunk: bytes, context: FilterExecutionContext) -> bool:
        """Detect when to start buffering - when we see content that might be lazy.

        We buffer all content to check if it ends with a lazy pattern.
        """
        try:
            chunk_str = chunk.decode("utf-8")
            if not chunk_str.startswith("data: "):
                return False
            payload_str = chunk_str[6:].strip()
            if payload_str == "[DONE]" or not payload_str:
                return False
            obj = json.loads(payload_str)
            choices = obj.get("choices", [])
            if not choices or not isinstance(choices[0], dict):
                return False
            delta = choices[0].get("delta", {})
            if not isinstance(delta, dict):
                return False
            # Start buffering when we see non-empty content
            # (will check for lazy pattern on flush).
            # Empty content ("") means no real content yet (e.g. a chunk
            # with role+empty-content that precedes reasoning_content).
            return "content" in delta and isinstance(delta.get("content"), str) and len(delta.get("content", "")) > 0
        except Exception:
            return False

    def _should_flush_buffer(self, chunk: bytes, context: FilterExecutionContext) -> bool:
        """Detect when to flush accumulated content buffer."""
        try:
            chunk_str = chunk.decode("utf-8")
            if not chunk_str.startswith("data: "):
                return False
            payload_str = chunk_str[6:].strip()
            if payload_str == "[DONE]" or not payload_str:
                return True  # Flush on [DONE]
            obj = json.loads(payload_str)
            choices = obj.get("choices", [])
            if not choices or not isinstance(choices[0], dict):
                return False
            delta = choices[0].get("delta", {})
            if not isinstance(delta, dict):
                return False
            # Check for finish_reason at the CHOICE level (standard OpenAI format)
            # as well as inside delta (some non-standard upstreams).
            fr = (choices[0].get("finish_reason") or delta.get("finish_reason"))
            # Flush when we see finish_reason
            if fr:
                log("DEBUG", "nudge_flush_finish_reason",
                    req_id=self._resolve_req_id(context) if context else "?",
                    finish_reason=fr)
                return True
            # Flush when content is no longer present
            # REMOVED: this caused premature flushes on tool_call or
            # reasoning_content chunks (which lack "content" in delta).
            # Only flush on finish_reason or [DONE], i.e. when the
            # upstream response is genuinely complete.
            # (See flush-on-finish_reason above and flush-on-[DONE] below)
        except Exception:
            pass
        return False

    async def _handle_intervention(self, context: FilterExecutionContext) -> StreamChunkResult:
        """Handle lazy response detection during streaming.

        This is called when buffering completes and we need to decide
        whether to retry or flush.

        If the buffer contains tool_calls, the model IS taking action —
        flush the buffer normally instead of triggering a retry.
        """
        req_id = self._resolve_req_id(context)

        # Get conversation
        conv = self._get_conversation(context)

        # Extract content from accumulated buffer
        combined = b"".join(self._stream_buffer)
        content = self._extract_content_from_buffer(combined)
        has_tool_calls_in_buffer = self._buffer_has_tool_calls(combined)

        # If the buffer contains tool_calls, the model IS acting — flush,
        # don't retry (the trailing ':' in text is natural punctuation).
        # ── Buffer output strategy ──────────────────────────────────
        # When _emit_while_buffering is True  → chunks were forwarded live,
        #                                       buffer flush emits nothing.
        # When _emit_while_buffering is False → buffer holds chunks that
        #                                       were NEVER yielded to the client.
        #                                       We must capture them for Phase 4
        #                                       WITHOUT yielding (otherwise Phase 4
        #                                       yields the same content again = dup).
        # Strategy: store the raw buffer bytes in context metadata so Phase 4
        # can rebuild merge_content from them.
        def _store_buffer_for_phase4(ctx, buf, combined):
            """Save buffer content and raw chunks for Phase 4 consumption."""
            text = self._extract_content_from_buffer(combined)
            ctx.metadata["_buffer_chunks"] = list(buf)
            ctx.metadata["_buffer_text"] = text
            ctx.metadata["_buffer_re_streamed"] = self._emit_while_buffering
            log("DEBUG", "nudge_buffer_stored",
                req_id=req_id, chunks=len(buf),
                content_len=len(text),
                combined_size=len(combined),
                content_preview=text[:100])
        if has_tool_calls_in_buffer:
            log("INFO", "nudge_streaming_skip_buffer_has_tc",
                req_id=req_id, content_length=len(content))
            _store_buffer_for_phase4(context, self._stream_buffer, combined)
            return StreamChunkResult(emit=[])

        if not content:
            # No content found — flush buffer normally
            _store_buffer_for_phase4(context, self._stream_buffer, combined)
            return StreamChunkResult(emit=[])

        # Check if content matches lazy pattern
        if not self._matches_lazy_response(content):
            # Not lazy — emit to Phase 4 only, skip Phase 2 yield
            _store_buffer_for_phase4(context, self._stream_buffer, combined)
            return StreamChunkResult(emit=[])

        # Lazy pattern detected — nudge the model to CONTINUE, not retry.
        # The original (lazy) content is NOT discarded — Phase 4 will merge
        # it with the continuation response.  Save the buffer here so the
        # original content survives into Phase 4.
        log("INFO", "nudge_streaming_lazy_detected",
            req_id=req_id, content=content[:200])

        # Save the buffer (original lazy content) for Phase 4 merging.
        # This is critical: without it, only the continuation response
        # would reach the client.
        _store_buffer_for_phase4(context, self._stream_buffer, combined)

        # Build augmented conversation
        augmented = copy.deepcopy(conv)
        augmented.append({
            "role": "assistant",
            "content": content,
        })

        # Execute retry
        upstream_model = self._resolve_upstream_model(context)
        upstream_url = self._resolve_upstream_url(context)

        if not upstream_model or not upstream_url:
            log("ERROR", "nudge_streaming_retry_missing_context", req_id=req_id)
            # Fall back — store buffer for Phase 4 (already forwarded if emit_while_buffering)
            _store_buffer_for_phase4(context, self._stream_buffer, combined)
            return StreamChunkResult(emit=[])

        # Clear buffer before retry
        self._stream_buffer = []

        # Build intervention message
        intervention_msg = {
            "role": "user",
            "content": self.config.nudge_message,
        }

        retry_decision = RetryDecision(
            messages=self._augment_conversation(augmented, intervention_msg),
            model=upstream_model,
            max_retries=self.config.max_retries,
            intervention_message=self.config.nudge_message,
        )

        return StreamChunkResult(retry=retry_decision)

    def _extract_content_from_buffer(self, combined: bytes) -> str:
        """Extract content from accumulated buffer.

        Extracts both ``delta.content`` (speech output) and
        ``delta.reasoning_content`` (internal reasoning) so that
        models that only emit reasoning (e.g. DeepSeek R1) don't
        produce empty responses after buffer extraction.
        """
        content_parts = []
        reasoning_parts = []
        try:
            combined_str = combined.decode("utf-8")
            for line in combined_str.split("\n"):
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload and payload != "[DONE]":
                        try:
                            obj = json.loads(payload)
                            choices = obj.get("choices", [])
                            if choices and isinstance(choices[0], dict):
                                delta = choices[0].get("delta", {})
                                if isinstance(delta, dict):
                                    content = delta.get("content")
                                    if content and isinstance(content, str):
                                        content_parts.append(content)
                                    rc = delta.get("reasoning_content")
                                    if rc and isinstance(rc, str):
                                        reasoning_parts.append(rc)
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass
        # If there is regular content, return that; otherwise fall back to
        # reasoning (so reasoning-only responses are not silently dropped).
        joined = "".join(content_parts).strip()
        if joined:
            return joined
        return "".join(reasoning_parts).strip()

    def _buffer_has_tool_calls(self, combined: bytes) -> bool:
        """Check if the streaming buffer contains tool_calls chunks."""
        try:
            combined_str = combined.decode("utf-8")
            for line in combined_str.split("\n"):
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload and payload != "[DONE]":
                        try:
                            obj = json.loads(payload)
                            choices = obj.get("choices", [])
                            if choices and isinstance(choices[0], dict):
                                delta = choices[0].get("delta", {})
                                if isinstance(delta, dict) and delta.get("tool_calls"):
                                    return True
                        except json.JSONDecodeError:
                            pass
        except Exception:
            pass
        return False



