"""ToolRewriteFinalizer — XML pseudo-tool-call → structured tool_calls delta.

Detects XML pseudo-tool-calls in ``AssistantTextDelta`` events and rewrites
them to structured ``ToolCallDelta`` events.

Supported XML patterns (via ``PseudoToolCallParser``):
1. Nested: ``<tool_name><arg1>val1</arg1><arg2>val2</arg2></tool_name>``
2. Separate: ``<name>tool_name</name><arg1>val1</arg1><arg2>val2</arg2>``
3. Function-param: ``<function=name><parameter=val</parameter>...``

Priority: 15 — runs before ``TimestampFinalizer`` (20) and
``ToolCallFinalizer`` (40).

Stateful/buffering behavior:
- Buffers XML content across multiple ``AssistantTextDelta`` events
- Emits replacement events only when XML is complete
- Handles incomplete buffered XML in ``finalize()`` by preserving text

Does NOT emit ``ToolCallComplete``, ``Finish``, ``Done``, or request recovery.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import List, Optional

from keeprollming.streaming.events import (
    AssistantTextDelta,
    StreamEvent,
    ToolCallDelta,
)
from keeprollming.streaming.finalizers import StreamFinalizer
from keeprollming.tool_rewrite import PseudoToolCallParser


class ToolRewriteFinalizer(StreamFinalizer):
    """Detect XML pseudo-tool-calls in AssistantTextDelta events and
    rewrite them to structured tool_calls format.

    Priority: 15 — runs before TimestampFinalizer (20) and
    ToolCallFinalizer (40).

    Structural text transforms that may remove/replace assistant text must
    run before tail-buffer finalizers that capture assistant text.

    Stateful/buffering behavior:
    - Buffers XML content across multiple AssistantTextDelta events
    - Emits replacement events only when XML is complete
    - Handles incomplete buffered XML in finalize() by preserving text

    Behavior:
    - process_event(AssistantTextDelta):
        - If content contains XML pseudo-tool-call:
            - Buffer XML content
            - If XML is complete:
                - Emit cleaned AssistantTextDelta (content without XML)
                - Emit ToolCallDelta with structured tool_calls
            - If XML is incomplete:
                - Continue buffering (return [])
        - Else:
            - Pass through unchanged
    - process_event(other):
        - Pass through unchanged
    - finalize():
        - If XML buffer is incomplete:
            - Emit buffered content as-is (preserve text rather than drop it)
        - Else:
            - Return empty list (XML was already emitted)
    """

    priority: int = 15

    def __init__(
        self,
        supported_patterns: Optional[List[str]] = None,
    ) -> None:
        self.supported_patterns = supported_patterns or [
            "nested", "separate", "function",
        ]
        self._parser = PseudoToolCallParser(
            supported_patterns=self.supported_patterns,
        )
        self._xml_buffer: List[str] = []
        self._buffering_xml = False
        self._xml_complete = False
        self._rewritten_tool_call_id: Optional[str] = None
        self._finalized: bool = False

    def reset(self, preserve_buffer: bool = False) -> None:
        """Reset internal state for recovery between attempts.

        Called by the stream runner between recovery attempts to clear buffered
        XML content that belongs to a failed attempt.

        Parameters
        ----------
        preserve_buffer:
            If True, preserve the XML buffer.
            ToolRewrite is deterministic and non-recovery, so preservation
            is not required for recovery semantics. This parameter is
            documented as harmless and not currently used by recovery.

        Safety
        ------
        * Safe to call before any events are processed.
        * Safe to call multiple times (idempotent).
        * Does not change normal no-recovery behavior.
        """
        if not preserve_buffer:
            self._xml_buffer = []
            self._buffering_xml = False
        self._xml_complete = False
        self._rewritten_tool_call_id = None
        self._finalized = False

    # ── StreamFinalizer contract ──────────────────────────────────

    def process_event(self, event: StreamEvent) -> list[StreamEvent]:
        """Process a single StreamEvent.

        * AssistantTextDelta → detect XML, buffer/rewrite
        * All other events → pass through unchanged
        """
        if isinstance(event, AssistantTextDelta):
            return self._process_assistant_text(event)
        return [event]

    def finalize(self) -> list[StreamEvent]:
        """Handle incomplete buffered XML at Finish.

        * If still buffering XML → emit buffered content as-is
        * Else → return empty list (XML was already emitted)

        Must be idempotent-safe: second call raises RuntimeError.
        """
        if self._finalized:
            raise RuntimeError("ToolRewriteFinalizer.finalize() already called")
        self._finalized = True

        if self._buffering_xml:
            # Incomplete XML at Finish — preserve it as text.
            combined = "".join(self._xml_buffer)
            self._xml_buffer = []
            self._buffering_xml = False
            return [AssistantTextDelta(delta=combined)]
        return []

    # ── Internal helpers ──────────────────────────────────────────

    def _process_assistant_text(
        self, event: AssistantTextDelta,
    ) -> list[StreamEvent]:
        """Process an AssistantTextDelta event.

        * If not buffering XML: check if content starts an XML tool call
        * If buffering XML: accumulate and check for completion
        * If XML complete: emit replacement events
        * If XML incomplete: continue buffering
        * If no XML: pass through
        """
        content = event.delta

        # If we already detected complete XML, just emit (pass through next
        # chunk — the XML was already rewritten)
        if self._xml_complete:
            self._xml_complete = False
            self._buffering_xml = False
            self._xml_buffer = []
            self._rewritten_tool_call_id = None
            return [event]

        # If not currently buffering, check if this chunk starts an XML tool
        # call
        if not self._buffering_xml:
            if self._is_xml_tool_call(content):
                self._buffering_xml = True
                self._xml_buffer = [content]
                # Check if it's complete immediately (single-chunk XML)
                if self._is_xml_complete(content):
                    self._xml_complete = True
                    self._buffering_xml = False
                    return self._rewrite_and_emit(content)
                return []  # Buffer this chunk

        # If buffering, accumulate
        if self._buffering_xml:
            self._xml_buffer.append(content)
            combined = "".join(self._xml_buffer)
            if self._is_xml_complete(combined):
                self._xml_complete = True
                self._buffering_xml = False
                # Rewrite the accumulated buffer and emit replacement
                return self._rewrite_and_emit(combined)
            return []  # Keep buffering

        # Normal pass-through
        return [event]

    def _is_xml_tool_call(self, content: str) -> bool:
        """Check if content contains XML pseudo-tool-call.

        Quick check: must have at least one XML-like tag and one of the known
        patterns (nested, separate, or function-param). Detects both complete
        and incomplete XML (for buffering across chunks).
        """
        if not content:
            return False

        # Quick check for XML-like structure
        if not re.search(r"<[a-zA-Z_][a-zA-Z0-9_-]*(?:\s|=|>)", content):
            return False

        # Check for known patterns
        return bool(
            "<name>" in content or
            "<function=" in content or
            PseudoToolCallParser.TAG_PATTERN.search(content) or
            # Detect incomplete XML: open tag without closing tag
            bool(re.search(r"<[a-zA-Z_][a-zA-Z0-9_-]*>", content))
        )

    def _is_xml_complete(self, content: str) -> bool:
        """Check if XML tool call is complete (has closing tag).

        Heuristic: check for common closing patterns. Must have ALL open
        tags with corresponding closing tags.
        """
        if "</function>" in content:
            return True

        # Check for nested pattern: <tool_name>...</tool_name>
        # Count open and closing tags for each tag name
        open_counts: dict[str, int] = {}
        close_counts: dict[str, int] = {}

        for match in re.finditer(r"<([a-zA-Z_][a-zA-Z0-9_-]*)(?:\s[^>]*)?>", content):
            tag = match.group(1).lower()
            open_counts[tag] = open_counts.get(tag, 0) + 1

        for match in re.finditer(r"</([a-zA-Z_][a-zA-Z0-9_-]*)>", content):
            tag = match.group(1).lower()
            close_counts[tag] = close_counts.get(tag, 0) + 1

        # Check if all open tags have corresponding closing tags
        for tag, count in open_counts.items():
            if close_counts.get(tag, 0) < count:
                return False

        return True

    def _rewrite_and_emit(
        self, content: str,
    ) -> list[StreamEvent]:
        """Rewrite accumulated XML buffer to structured tool_calls.

        Returns
        -------
        list[StreamEvent]
            Replacement events: cleaned AssistantTextDelta + ToolCallDelta.
        """
        # Extract the parseable XML portion from surrounding assistant text.
        xml_content = self._extract_xml_from_content(content)

        # Parse pseudo-tool-call from the extracted XML
        pseudo_call = self._parser.parse(xml_content)

        if not pseudo_call:
            # Fall back to emitting original buffer as-is
            return [AssistantTextDelta(delta=content)]

        # Build structured tool call
        tool_call_id = f"call_{uuid.uuid4().hex[:8]}"
        arguments_json = json.dumps(pseudo_call.arguments, separators=(",", ":"))

        # Clean content (remove XML)
        cleaned = self._clean_content(content)

        # Emit replacement events
        events: List[StreamEvent] = []
        if cleaned.strip():
            events.append(AssistantTextDelta(delta=cleaned))
        events.append(ToolCallDelta(
            index=0,
            id=tool_call_id,
            name=pseudo_call.name,
            arguments_delta=arguments_json,
        ))

        self._rewritten_tool_call_id = tool_call_id
        return events

    def _extract_xml_from_content(self, content: str) -> str:
        """Extract XML pseudo-tool-call from content.

        Extract the XML portion from content that may have surrounding text.
        """
        # Try to find XML pattern in content
        # Look for <name>...</name> pattern (separate pattern)
        name_match = re.search(
            r'<name>([^<]*)</name>',
            content, re.IGNORECASE | re.DOTALL,
        )
        if name_match:
            # Extract everything from <name> to the end of the XML
            start = content.find('<name>')
            # Find the end of the XML (after the last argument tag)
            # For now, extract everything from <name> to the end of content
            # Separate-pattern parsing consumes the remaining XML structure.
            return content[start:]

        # Look for function-param pattern: <function=...>...</function>
        # This must precede the generic nested extraction: an outer
        # ``<tool_call>`` wrapper otherwise hides the parseable function tag.
        func_match = PseudoToolCallParser.FUNCTION_PATTERN.search(content)
        if func_match:
            return func_match.group(0)

        # Look for nested pattern: <tool_name>...</tool_name>
        # For nested XML, we need to extract the entire XML, not just inner tags
        # Find the first < and the last > in the content
        first_lt = content.find('<')
        last_gt = content.rfind('>')
        if first_lt >= 0 and last_gt > first_lt:
            # Extract everything from the first < to the last >
            return content[first_lt:last_gt + 1]

        # If no XML pattern found, return original content
        return content

    def _clean_content(self, content: str) -> str:
        """Remove XML pseudo-tool-call from content.

        Preserves surrounding text. Handles nested XML by recursively
        removing inner tags until all XML is removed.
        """
        # The function-param dialect is often wrapped in ``<tool_call>``.
        # Remove that complete wrapper first; the generic nested regex below
        # intentionally cannot match nested tags.
        cleaned = re.sub(
            r"<tool_call>\s*<function=[^>]*>[\s\S]*?</function>\s*</tool_call>",
            "",
            content,
            flags=re.IGNORECASE,
        )

        # Remove nested pattern: <tool_name>...</tool_name>
        # Use the pattern string directly (not compiled) to avoid flags conflict
        nested_pattern = PseudoToolCallParser.TAG_PATTERN.pattern

        # Recursively remove nested XML tags until no more matches
        prev = None
        # Continue from the wrapper-cleaned representation.
        while prev != cleaned:
            prev = cleaned
            cleaned = re.sub(
                nested_pattern,
                '', cleaned, flags=re.DOTALL,
            )

        # Remove separate pattern: <name>...</name>
        cleaned = re.sub(
            r'<name>[^<]*</name>',
            '', cleaned, flags=re.IGNORECASE | re.DOTALL,
        )
        # Remove function-param pattern: <function=...>...</function>
        cleaned = re.sub(
            r'<function=[^>]*>[\s\S]*?</function>',
            '', cleaned, flags=re.IGNORECASE | re.DOTALL,
        )
        return cleaned.strip()

    # ── State accessors (for testing/debugging) ───────────────────

    @property
    def is_buffering(self) -> bool:
        """Whether currently buffering XML."""
        return self._buffering_xml

    @property
    def rewritten_tool_call_id(self) -> Optional[str]:
        """The tool call ID generated by the last rewrite."""
        return self._rewritten_tool_call_id
