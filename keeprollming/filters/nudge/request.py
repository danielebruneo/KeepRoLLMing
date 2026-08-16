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

Streaming is handled by the stream finalizer pipeline.
"""

import copy
import json
import re
import uuid
from typing import List, Optional

import inspect
import httpx

from keeprollming.orchestrator.filter import (
    FilterConfig,
    FilterExecutionContext,
    Request,
    Response,
    Filter,
)
from keeprollming.orchestrator.filters.events import emit_nudge_detected
from keeprollming.logger import log
from keeprollming.filters.nudge import ModelNudgeConfig


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


class ModelNudgeFilter(Filter):
    """
    Filter that detects lazy LLM responses and handles retry internally.

    Configuration:
        enabled (bool): Enable/disable filter (default: True)
        trigger_patterns (List[str]): Regex patterns matching end of lazy responses
        action (str): "nudge" or "regenerate" (default: "nudge")
        nudge_message (str): Message to inject when action="nudge" (default: "Continue.")
        max_attempts (int): Maximum consecutive nudges before giving up (default: 3)

    Example config:
        model_nudge:
          enabled: true
          trigger_patterns: [":$", "\\bnow I will\\b", "\\bhere's the plan\\b"]
          action: nudge
          nudge_message: "Continue, you need to produce a tool call."
          max_attempts: 3

    Usage in route config:
        routes:
          - name: my_route
            model: anthropic/claude-3
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
                max_attempts=config.get("max_attempts", 3),
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
                    nudge_on_empty=True, content="[empty]", max_attempts=self.config.max_attempts)
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
        
        max_attempts = self.config.max_attempts
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

        emit_nudge_detected(
            context,
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
            


            # Production Pipeline supplies an upstream caller. Direct HTTP is
            # retained for explicit standalone-filter use in focused tests.
            if context._upstream_caller:
                # canonical Pipeline: upstream_caller may be an async generator (SSE chunks)
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
                # Standalone filter use: make the retry directly.
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
