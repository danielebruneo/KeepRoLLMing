"""TimestampFinalizer — tail-buffer finalizer for canonical streaming pipeline.

Strips stale timestamp footers from the final tail of assistant content and
appends exactly one fresh footer derived from the configured template.

This module is **independent** of the previous pipeline and can be unit-tested in
isolation.  It reuses the same template-driven strip semantics as
``TimestampFilter._strip_existing_timestamp_footer`` without modifying it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable, List, Optional
from zoneinfo import ZoneInfo

from keeprollming.streaming.events import AssistantTextDelta, StreamEvent
from keeprollming.streaming.finalizers import StreamFinalizer


# ---------------------------------------------------------------------------
# Shared strip logic (mirrors TimestampFilter internals)
# ---------------------------------------------------------------------------


def _build_strip_regex(template: str) -> "re.Pattern[str] | None":
    """Build a regex that matches a complete timestamp footer at end of content.

    Uses the template's static prefix (before the first ``%`` directive) and
    the literal suffix after the last strftime ``%`` directive.

    Returns a compiled regex or ``None`` if the template has no static prefix.
    """
    pct_idx = template.find('%')
    if pct_idx < 0:
        return None

    prefix = template[:pct_idx]
    if not prefix:
        return None

    lines = prefix.split('\n')
    escaped = [re.escape(p) for p in lines]
    regex_str = r'\n*'.join(escaped)
    # Make opening bracket optional — model may echo without brackets
    regex_str = regex_str.replace(r'\[', r'\[?')

    # Suffix after the last strftime directive
    strftime_part = template[pct_idx:]
    last_pct = strftime_part.rfind('%')
    suffix = strftime_part[last_pct + 2:] if last_pct + 2 <= len(strftime_part) else ''
    suffix_pattern = re.escape(suffix) if suffix else ''

    # Use \s*$ to tolerate trailing whitespace after the footer.
    full_pattern = regex_str + r'.+' + suffix_pattern + r'\s*$'
    return re.compile(full_pattern, re.DOTALL)


def _strip_existing_timestamp_footer(content: str, template: str) -> str:
    """Strip ALL consecutive matching timestamp footers from the end of *content*.

    Only strips if the content ends with a footer matching the configured
    template.  Timestamp-looking text in the middle of the response is
    preserved.

    After stripping, appending a fresh footer produces exactly one footer.

    Parameters
    ----------
    content:
        The assistant text content to strip.
    template:
        The timestamp template (e.g. ``"\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"``).

    Returns
    -------
    str
        Content with all matching final footers stripped.
    """
    pct_idx = template.find('%')
    if not content or pct_idx < 0 or pct_idx == 0:
        return content

    prefix = template[:pct_idx]
    if not prefix:
        return content

    footer_re = _build_strip_regex(template)
    if footer_re is None:
        return content

    if not re.search(footer_re, content):
        return content

    # Build prefix pattern for walk
    lines = prefix.split('\n')
    escaped = [re.escape(p) for p in lines]
    prefix_pattern = r'\n*'.join(escaped)
    prefix_pattern = prefix_pattern.replace(r'\[', r'\[?')

    matches = list(re.finditer(prefix_pattern, content))
    if not matches:
        return content

    strftime_part = template[pct_idx:]
    last_pct = strftime_part.rfind('%')
    suffix = strftime_part[last_pct + 2:] if last_pct + 2 <= len(strftime_part) else ''

    strip_start = None
    for i in range(len(matches) - 1, -1, -1):
        m = matches[i]
        if i < len(matches) - 1:
            next_m = matches[i + 1]
            next_tail = content[next_m.end():]
            next_is_clean = '\n' not in next_tail.rstrip()
            if next_is_clean:
                between = content[m.end():next_m.start()]
                between_stripped = between.strip()
                if between_stripped:
                    if prefix not in between_stripped:
                        lines_in_between = between_stripped.split('\n')
                        if len(lines_in_between) > 1:
                            last_line = lines_in_between[-1]
                            if re.match(prefix_pattern, last_line):
                                strip_start = m.start()
                            else:
                                if strip_start is None:
                                    strip_start = m.start()
                                break
                        else:
                            strip_start = m.start()
                    elif re.match(prefix_pattern + r'.+', between_stripped, re.DOTALL):
                        strip_start = m.start()
                    else:
                        if strip_start is None:
                            strip_start = m.start()
                        break
                else:
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
                            # Single-line timestamp value — consecutive block.
                            strip_start = m.start()
                        else:
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
                    strip_start = m.start()
        else:
            tail_from_match = content[m.end():]
            if '\n' not in tail_from_match.rstrip():
                if strip_start is None:
                    strip_start = m.start()

    if strip_start is not None:
        return content[:strip_start].rstrip()
    return content


# ---------------------------------------------------------------------------
# TimestampFinalizer
# ---------------------------------------------------------------------------


class TimestampFinalizer(StreamFinalizer):
    """Strip stale timestamps from the final tail, append fresh one.

    Uses a tail-buffer (rolling window) so only text near the end of the
    response is retained in memory.  Safe text that falls out of the tail
    window is emitted immediately as ``AssistantTextDelta`` objects.

    Implements the ``StreamFinalizer`` contract for streaming pipeline integration.

    Parameters
    ----------
    template:
        A strftime-compatible template string.  Example:
        ``"\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"``
    timezone:
        Timezone name for ``ZoneInfo``. Defaults to ``"UTC"``.
    tail_buffer_size:
        Maximum characters to retain in the rolling tail buffer.
        Default ``1024``.
    clock:
        Optional callable returning a ``datetime`` for deterministic testing.
    """

    priority: int = 20  # Runs before nudge (50)

    def __init__(
        self,
        template: str,
        timezone: str = "UTC",
        tail_buffer_size: int = 1024,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        super().__init__()
        self.template = template
        self._timezone = timezone
        self.tail_buffer_size = tail_buffer_size
        self._clock = clock
        self._tail_buffer = ""
        self._finalized = False
        self._final_delta: Optional[AssistantTextDelta] = None

    # ── public API (streaming contract) ──────────────────────────────────

    def reset(self, preserve_buffer: bool = True) -> None:
        """Reset finalizer state for a new recovery attempt.

        B2 recovery integration: when the runner restarts the upstream
        stream after a recovery decision, this method is called to reset
        the finalizer for the new attempt.

        Args:
            preserve_buffer: If True (default for append_continuation),
                preserve _tail_buffer for continuation.
                If False (for replace strategy), clear all buffers.
        """
        self._finalized = False
        self._final_delta = None
        if not preserve_buffer:
            self._tail_buffer = ""

    def process_event(self, event: StreamEvent) -> list[StreamEvent]:
        """Buffer ``AssistantTextDelta`` via ``process_delta``; pass-through others.

        When the event is an ``AssistantTextDelta`` the finalizer buffers
        the delta text in its tail-buffer and returns any safe-prefix
        ``AssistantTextDelta`` events that have fallen out of the tail
        window.

        For non-text events the finalizer returns the event unchanged
        (pass-through).
        """
        if isinstance(event, AssistantTextDelta):
            return self.process_delta(event.delta)
        return [event]

    def process_delta(self, delta: str) -> list[AssistantTextDelta]:
        """Accept assistant text delta. Buffer tail; emit safe prefix.

        Parameters
        ----------
        delta:
            Raw assistant text from an ``AssistantTextDelta``.

        Returns
        -------
        list[AssistantTextDelta]
            Emitted safe text that has fallen out of the tail window.
            Empty if the tail buffer is still below ``tail_buffer_size``.

        Raises
        ------
        RuntimeError:
            If ``finalize()`` has already been called.
        """
        if self._finalized:
            raise RuntimeError("TimestampFinalizer.finalize() already called")

        combined = self._tail_buffer + delta
        if len(combined) > self.tail_buffer_size:
            emit_part = combined[: len(combined) - self.tail_buffer_size]
            self._tail_buffer = combined[len(emit_part) :]
            return [AssistantTextDelta(delta=emit_part)]
        else:
            self._tail_buffer = combined
            return []

    def finalize(self) -> list[AssistantTextDelta]:
        """Strip stale timestamp footer(s) from tail; append fresh footer.

        Returns
        -------
        list[AssistantTextDelta]
            A list containing a single delta with the corrected final tail.

        Raises
        ------
        RuntimeError:
            If called multiple times (not idempotent).
        """
        if self._finalized:
            raise RuntimeError("TimestampFinalizer.finalize() already called")
        self._finalized = True

        stripped = _strip_existing_timestamp_footer(
            self._tail_buffer, self.template
        ).rstrip()
        # A tool-call turn can legitimately contain no assistant text.  Do
        # not manufacture a timestamp-only response in that case.
        if not stripped:
            return []
        fresh = self._format_timestamp()
        result = f"{stripped}{fresh}"
        self._final_delta = AssistantTextDelta(delta=result)
        return [self._final_delta]

    # ── internal helpers ────────────────────────────────────────────

    def _format_timestamp(self) -> str:
        """Return the template string with strftime directives expanded."""
        try:
            tz = ZoneInfo(self._timezone)
        except Exception:
            tz = timezone.utc
        if self._clock is not None:
            return self._clock().strftime(self.template)
        return datetime.now(tz).strftime(self.template)

    @property
    def finalized(self) -> bool:
        """Whether ``finalize()`` has been called."""
        return self._finalized
