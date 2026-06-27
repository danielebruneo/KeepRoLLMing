"""
Timestamp Filter — injects request/response timestamps.

Request side:
    Injects a system message with the current UTC timestamp at the end
    of the messages list (after all existing messages).

Response side:
    Appends a template string (with strftime timestamp) to the assistant
    message content. If no assistant content exists (e.g., tool_calls-only),
    the response is left unchanged unless always=true.

Configuration:
    timestamp:
        enabled: true
        template: "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"
        timezone: "UTC"
        always: false

The template is a strftime-compatible string.  % directives are expanded
to the current datetime.  Example templates:

    "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"    (default)
    "\n\n---\n[%H:%M:%S]"
    "\n\n---\nUTC: %Y-%m-%d %H:%M:%S"
    "\n\n---\n🕐 %d/%m/%Y %H:%M:%S %Z"
"""

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..filter import Filter, FilterConfig, FilterExecutionContext, Request, Response, register_filter
from keeprollming.logger import log


@register_filter("timestamp")
class TimestampFilter(Filter):
    """Inject UTC timestamp into request and response.

    Priority: 100 (runs last, after all other filters).
    """

    _default_name = "timestamp"
    priority = 100
    _DEFAULT_TEMPLATE = "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"

    def __init__(self, config=None, **kwargs):
        if config is None:
            config = {}
        if isinstance(config, dict):
            enabled = config.get("enabled", kwargs.get("enabled", True))
            self._template = config.get("template", self._DEFAULT_TEMPLATE)
            self._timezone = config.get("timezone", "UTC")
            self._always = config.get("always", False)
        elif isinstance(config, FilterConfig):
            enabled = config.enabled
            self._template = kwargs.get("template", self._DEFAULT_TEMPLATE)
            self._timezone = kwargs.get("timezone", "UTC")
            self._always = kwargs.get("always", False)
        else:
            enabled = config
            self._template = kwargs.get("template", self._DEFAULT_TEMPLATE)
            self._timezone = kwargs.get("timezone", "UTC")
            self._always = kwargs.get("always", False)

        # Extract just the strftime format from the template for the request system message.
        # Find everything from the first % directive to end of string.
        idx = self._template.find('%')
        self._bare_format = self._template[idx:] if idx >= 0 else "%Y-%m-%d %H:%M:%S UTC"
        self._static_prefix = self._template[:idx] if idx >= 0 else ""

        filter_config = FilterConfig(enabled=enabled, name="timestamp")
        super().__init__(filter_config)

    # ── Helpers ──────────────────────────────────────────────────────

    def _format_timestamp(self) -> str:
        """Return the template string with strftime directives expanded."""
        try:
            tz = ZoneInfo(self._timezone)
        except Exception:
            tz = timezone.utc
        return datetime.now(tz).strftime(self._template)

    def _bare_timestamp(self) -> str:
        """Return just the timestamp portion (no separator/prefix) for request system msg."""
        try:
            tz = ZoneInfo(self._timezone)
        except Exception:
            tz = timezone.utc
        return datetime.now(tz).strftime(self._bare_format)

    def _content_has_timestamp_footer(self, content: str) -> bool:
        """Check if content already ends with a timestamp footer matching template.

        Uses the template's ``_static_prefix`` (everything before the first
        ``%`` directive) with flexible newlines so the model's echoed
        timestamp is recognised even when newline counts differ.

        Supports ANY template format — it only relies on the static prefix.
        """
        if not content or not self._static_prefix:
            return False
        import re as _re
        prefix = self._static_prefix
        # Split by actual newlines, escape each part, join with \n*
        # (\n* = flexible newlines between the static prefix segments).
        lines = prefix.split('\n')
        escaped_lines = [_re.escape(part) for part in lines]
        regex_str = r'\n*'.join(escaped_lines)
        # Make the opening bracket optional — the model may echo the
        # timestamp WITHOUT brackets even when the template has them.
        regex_str = regex_str.replace(r'\[', r'\[?')
        # Match: flexible-newline prefix + timestamp value + end
        return bool(_re.search(regex_str + r'[^\n]*$', content))

    # ── Request phase ────────────────────────────────────────────────

    async def process_request(
        self,
        request: Request,
        context: FilterExecutionContext,
    ) -> Request:
        """Inject a system message with the current timestamp.

        Updates the existing timestamp system message in-place if one exists
        (identified by "Current UTC time:" prefix).  Otherwise appends a new
        one at the end.  This prevents system-message accumulation across
        multiple conversation turns.
        """
        if not self.is_enabled:
            return request

        req_id = self._resolve_req_id(context)
        timestamp_str = self._bare_timestamp()

        # Update existing timestamp system message if found, otherwise append
        found = False
        for msg in request.messages:
            if (isinstance(msg, dict) and msg.get("role") == "system"
                    and isinstance(msg.get("content"), str)
                    and msg["content"].startswith("Current UTC time:")):
                msg["content"] = f"Current UTC time: {timestamp_str}"
                found = True
                break

        if not found:
            request.messages.append({
                "role": "system",
                "content": f"Current UTC time: {timestamp_str}",
            })

        context.state["timestamp_str"] = timestamp_str

        log("INFO", "timestamp_injected", req_id=req_id, timestamp=timestamp_str,
            updated_in_place=found)
        return request

    # ── Response phase ──────────────────────────────────────────────

    async def process_response(
        self,
        response: Response,
        context: FilterExecutionContext,
    ) -> Response:
        """Append timestamp to assistant message content if present."""
        if not self.is_enabled:
            return response

        req_id = self._resolve_req_id(context)

        # Generate the footer string (template with strftime expanded)
        footer = self._format_timestamp()

        content = response.content
        has_tc = hasattr(response, 'tool_calls') and bool(response.tool_calls)

        if not content and not has_tc:
            return response

        # Replace any stale timestamp footer with a fresh one
        # (the LLM may have echoed back conversation history with prior
        # timestamps, masking lazy patterns from the nudge filter).
        if isinstance(content, str) and self._content_has_timestamp_footer(content):
            log("INFO", "timestamp_replacing_stale", req_id=req_id,
                original_len=len(content))
            import re as _re
            pattern = _re.compile(
                r"\n*---\n\[?Timestamp: .+?(?: UTC)?$",
                _re.DOTALL,
            )
            content = pattern.sub("", content).rstrip()
            log("INFO", "timestamp_stripped_stale", req_id=req_id,
                stripped_len=len(content))
            # Fall through — fresh footer is appended below

        if not content and has_tc:
            if not self._always:
                return response
            new_content = footer
        else:
            new_content = f"{content}{footer}"

        new_response = type(response)(
            content=new_content,
            model=response.model,
            finish_reason=response.finish_reason,
            tool_calls=response.tool_calls,
            usage=response.usage,
            reasoning_content=getattr(response, 'reasoning_content', ''),
        )

        log("INFO", "timestamp_appended", req_id=req_id,
            original_length=len(content), new_length=len(new_content))

        return new_response

    def reset(self) -> None:
        pass
