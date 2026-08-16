"""
Timestamp Filter — injects request/response timestamps.

Request side:
    Injects a system message with the current UTC timestamp at the end
    of the messages list (after all existing messages).

Response side:
    Appends a template string (with strftime timestamp) to the assistant
    message content. If no assistant content exists (e.g., tool_calls-only),
    the response is left unchanged (no timestamp assistant content on tool-call-only turns).

Configuration:
    timestamp:
        enabled: true
        template: "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"
        timezone: "UTC"

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

from keeprollming.orchestrator.filter import (
    Filter,
    FilterConfig,
    FilterExecutionContext,
    Request,
    Response,
)
from keeprollming.logger import log


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
        elif isinstance(config, FilterConfig):
            enabled = config.enabled
            self._template = kwargs.get("template", self._DEFAULT_TEMPLATE)
            self._timezone = kwargs.get("timezone", "UTC")
        else:
            enabled = config
            self._template = kwargs.get("template", self._DEFAULT_TEMPLATE)
            self._timezone = kwargs.get("timezone", "UTC")

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
        # Use \s*$ to tolerate trailing whitespace after the footer.
        return bool(_re.search(regex_str + r'[^\n]*\s*$', content))

    def _build_strip_regex(self) -> re.Pattern | None:
        """Build a regex that matches the LAST timestamp footer in content.

        Uses the template's ``_static_prefix`` and suffix (text after the last
        strftime ``%`` directive) to construct a pattern that matches a complete
        footer at the end of a string.

        Returns a compiled regex whose group(1) captures everything BEFORE the
        last footer, or ``None`` if the template has no static prefix.
        """
        prefix = self._static_prefix
        if not prefix:
            return None

        lines = prefix.split('\n')
        escaped = [re.escape(p) for p in lines]
        regex_str = r'\n*'.join(escaped)
        # Make the opening bracket optional — the model may echo the
        # timestamp WITHOUT brackets even when the template has them.
        regex_str = regex_str.replace(r'\[', r'\[?')

        # Extract the literal suffix after the last strftime directive.
        # strftime directives are always 2 chars (%X), so suffix starts at
        # last_pct + 2.
        strftime_part = self._template[self._template.find('%'):]
        last_pct = strftime_part.rfind('%')
        suffix = strftime_part[last_pct + 2:] if last_pct + 2 <= len(strftime_part) else ''
        suffix_pattern = re.escape(suffix) if suffix else ''

        # Pattern: prefix + .+ (strftime value) + suffix + end-of-string
        # Use \s*$ to tolerate trailing whitespace after the footer.
        full_pattern = regex_str + r'.+' + suffix_pattern + r'\s*$'
        return re.compile(full_pattern, re.DOTALL)

    def _strip_existing_timestamp_footer(self, content: str) -> str:
        """Strip ALL consecutive matching timestamp footers from the end of *content*.

        Only strips if the content ends with a footer matching the configured
        template.  If multiple footers exist (e.g. stale + fresh), finds the
        FIRST footer in the final consecutive block and strips from there to the
        end — returning only the base content with no final timestamp footer.

        Timestamp-looking text in the middle of the response is preserved.

        After stripping, appending a fresh footer produces exactly one footer.

        This is designed for use in ``process_response`` where a fresh footer
        will be appended immediately after stripping.  For the direct streaming
        e2e path (previous), Phase 2 already emitted the stale footer to the client,
        so this helper alone cannot dedup in that path — a canonical tail-buffer
        ``TimestampFinalizer`` is required for pre-emission stripping.
        """
        if not content or not self._static_prefix:
            return content

        footer_re = self._build_strip_regex()
        if footer_re is None:
            return content

        # First, verify the content actually ends with a footer.
        if not re.search(footer_re, content):
            return content

        # For templates with no suffix (e.g. "### Time: %Y-%m-%d"), verify
        # the last prefix match's tail is a single-line timestamp value
        # (no embedded newlines), ensuring it's a fresh footer rather than
        # a stale one with trailing text.
        strftime_part = self._template[self._template.find('%'):]
        last_pct = strftime_part.rfind('%')
        suffix = strftime_part[last_pct + 2:] if last_pct + 2 <= len(strftime_part) else ''

        # Find all prefix matches.
        prefix = self._static_prefix
        lines = prefix.split('\n')
        escaped = [re.escape(p) for p in lines]
        prefix_pattern = r'\n*'.join(escaped)
        prefix_pattern = prefix_pattern.replace(r'\[', r'\[?')

        matches = list(re.finditer(prefix_pattern, content))

        # Walk from LAST to FIRST, finding the start of the final consecutive
        # block of footers.  A match is part of the final block if the content
        # from the NEXT match's end to the string end is a clean single-line
        # footer value (no embedded newlines after rstrip).
        #
        # For consecutive footers: match[1] (last) has a clean tail.  match[0]
        # has next=match[1], and match[1]'s tail is clean -> match[0] is in
        # the block.  Strip from match[0].
        #
        # For middle+end: match[1] (last) has a clean tail.  match[0] has
        # next=match[1], and match[1]'s tail is clean -> match[0] is in the
        # block.  But match[0]'s tail has non-footer text between it and
        # match[1] (e.g. "More text").
        #
        # Heuristic: the final block starts at the earliest match where either:
        #   - It's the only match (single footer), OR
        #   - The next match's tail is clean AND the between text contains no
        #     footer prefix pattern (between is just timestamp value + whitespace).
        strip_start = None
        for i in range(len(matches) - 1, -1, -1):
            m = matches[i]
            # Check if this match starts a new block.
            # A new block starts when the next match (if any) has a clean tail
            # (no embedded newlines) but the between text has non-footer content.
            if i < len(matches) - 1:
                next_m = matches[i + 1]
                next_tail = content[next_m.end():]
                next_is_clean = '\n' not in next_tail.rstrip()
                if next_is_clean:
                    # Next match is clean. Check if between text is just
                    # timestamp value (consecutive) or has non-footer text.
                    between = content[m.end():next_m.start()]
                    between_stripped = between.strip()
                    if between_stripped:
                        # Check if between text is just a timestamp value
                        # (consecutive footers) or has non-footer text (middle
                        # footer).  A timestamp value doesn't contain the
                        # prefix pattern.
                        if prefix not in between_stripped:
                            # Between text doesn't contain the prefix. Check if
                            # it's just a timestamp value (consecutive) or has
                            # non-footer text (middle footer).
                            # A timestamp value is a single line. If there are
                            # multiple lines, check if the last line starts
                            # with the prefix pattern (next footer) or is
                            # non-footer text.
                            lines_in_between = between_stripped.split('\n')
                            if len(lines_in_between) > 1:
                                # Multiple lines — check if the last line starts
                                # with the prefix pattern (next footer) or is
                                # non-footer text.
                                last_line = lines_in_between[-1]
                                if re.match(prefix_pattern, last_line):
                                    # Last line is the next footer's prefix —
                                    # consecutive block.
                                    strip_start = m.start()
                                else:
                                    # Last line is non-footer text — middle
                                    # footer.
                                    if strip_start is None:
                                        strip_start = m.start()
                                    break
                            else:
                                # Single line — just a timestamp value.
                                # Consecutive block.
                                strip_start = m.start()
                        elif re.match(prefix_pattern + r'.+', between_stripped, re.DOTALL):
                            # Between text starts with prefix + value — consecutive
                            # block, continue to previous match.
                            strip_start = m.start()
                        else:
                            # Between text contains the prefix but doesn't start
                            # with it — non-footer text (e.g. "More text\n---\n
                            # Timestamp: ...").  This match starts a new block.
                            if strip_start is None:
                                strip_start = m.start()
                            break
                    else:
                        # Empty between text — consecutive block.
                        strip_start = m.start()
                else:
                    # Next match's tail has newlines — likely because there are
                    # more matches after it (3+ consecutive footers).  Check if
                    # the between text is a single-line timestamp value to
                    # extend the consecutive block backward.
                    between = content[m.end():next_m.start()]
                    between_stripped = between.strip()
                    if between_stripped:
                        if prefix not in between_stripped:
                            lines_in_between = between_stripped.split('\n')
                            if len(lines_in_between) == 1:
                                # Single-line timestamp value — consecutive
                                # block, extend strip_start.
                                strip_start = m.start()
                            else:
                                # Multi-line — check last line for prefix.
                                last_line = lines_in_between[-1]
                                if re.match(prefix_pattern, last_line):
                                    strip_start = m.start()
                                else:
                                    if strip_start is None:
                                        strip_start = m.start()
                                    break
                        elif re.match(prefix_pattern + r'.+', between_stripped, re.DOTALL):
                            strip_start = m.start()
                        else:
                            if strip_start is None:
                                strip_start = m.start()
                            break
                    else:
                        # Empty between text — consecutive block.
                        strip_start = m.start()
            else:
                # Last match — always part of the final block.
                # But verify the tail is clean (no embedded newlines after
                # rstrip) — if not, the footer is in the middle of content
                # and should not be stripped.
                tail_from_match = content[m.end():]
                if '\n' not in tail_from_match.rstrip():
                    if strip_start is None:
                        strip_start = m.start()
                # Don't break — continue to check if an earlier match is also
                # part of the consecutive block.

        if strip_start is not None:
            return content[:strip_start].rstrip()

        return content

    def _is_content_ending_with_footer(self, content: str) -> bool:
        """Check if *content* ends with a timestamp footer matching the template.

        This is a stricter version of ``_content_has_timestamp_footer`` that
        handles templates with an empty suffix (e.g. ``"### Time: %Y-%m-%d"``)
        by also verifying the tail from the last prefix match is reasonably
        short — i.e. looks like a fresh footer rather than a stale one with
        trailing text.
        """
        if not content or not self._static_prefix:
            return False

        footer_re = self._build_strip_regex()
        if footer_re is None:
            return False

        # Quick check: does the content end with a footer pattern?
        if not re.search(footer_re, content):
            return False

        # For templates with no suffix (empty strftime_and_suffix tail),
        # verify the last prefix match's tail is short enough to be a fresh footer.
        strftime_part = self._template[self._template.find('%'):]
        last_pct = strftime_part.rfind('%')
        suffix = strftime_part[last_pct + 2:] if last_pct + 2 <= len(strftime_part) else ''

        if not suffix:
            # No suffix — check that the last prefix match's tail is short.
            prefix = self._static_prefix
            lines = prefix.split('\n')
            escaped = [re.escape(p) for p in lines]
            prefix_pattern = r'\n*'.join(escaped)
            prefix_pattern = prefix_pattern.replace(r'\[', r'\[?')

            matches = list(re.finditer(prefix_pattern, content))
            if matches:
                last_match = matches[-1]
                tail = content[last_match.end():]
                # Fresh footer timestamp values are typically < 100 chars.
                return len(tail) < 100

        return True

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
        if isinstance(content, str) and self._is_content_ending_with_footer(content):
            log("INFO", "timestamp_replacing_stale", req_id=req_id,
                original_len=len(content))
            content = self._strip_existing_timestamp_footer(content)
            log("INFO", "timestamp_stripped_stale", req_id=req_id,
                stripped_len=len(content))
            # Fall through — fresh footer is appended below

        if not content and has_tc:
            # No assistant content, only tool_calls: do NOT emit timestamp.
            return response
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
