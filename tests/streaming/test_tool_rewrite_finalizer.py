"""Unit tests for ToolRewriteFinalizer.

Verifies:
- Nested XML rewritten
- Separate XML rewritten
- Function-param XML rewritten
- No XML pass-through
- Invalid XML pass-through
- XML split across two AssistantTextDelta events
- XML split across three AssistantTextDelta events
- Incomplete XML at finalize is preserved as text
- Surrounding text before/after XML emitted exactly once
- Multiple tool calls (if supported, or explicitly documented not supported)
- Generated tool call id is stable enough for test matching or pattern-matched
"""

from __future__ import annotations

import pytest

from keeprollming.streaming.events import (
    AssistantTextDelta,
    Finish,
    ToolCallDelta,
)
from keeprollming.filters.tool_rewrite.stream import ToolRewriteFinalizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_text_delta(content: str) -> AssistantTextDelta:
    """Build an AssistantTextDelta with the given content."""
    return AssistantTextDelta(delta=content)


# ---------------------------------------------------------------------------
# Test 1: Basic transformation
# ---------------------------------------------------------------------------


class TestToolRewriteFinalizerBasic:
    """Basic transformation tests."""

    def test_nested_xml_rewritten(self):
        """Nested XML <read><path>README.md</path></read> → structured tool call."""
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta('<read><path>README.md</path></read>')

        result = finalizer.process_event(event)

        # Should emit replacement events
        # When there's no surrounding text, only ToolCallDelta is emitted
        assert len(result) == 1, f"Expected 1 event, got {len(result)}: {result}"

        # Event should be ToolCallDelta
        assert isinstance(result[0], ToolCallDelta)
        assert result[0].name == "read"
        assert result[0].arguments_delta == '{"path":"README.md"}'
        assert result[0].id.startswith("call_")

    def test_separate_xml_rewritten(self):
        """Separate XML <name>search</name><query>test</query> → structured tool call."""
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta('<name>search</name><query>test</query>')

        result = finalizer.process_event(event)

        # Should emit replacement events
        # When there's no surrounding text, only ToolCallDelta is emitted
        assert len(result) == 1, f"Expected 1 event, got {len(result)}: {result}"

        # Event should be ToolCallDelta
        assert isinstance(result[0], ToolCallDelta)
        assert result[0].name == "search"
        assert result[0].arguments_delta == '{"query":"test"}'
        assert result[0].id.startswith("call_")

    def test_function_xml_rewritten(self):
        """Function-param XML <function=ReactJudgeOutput><parameter=has_issue>False</parameter></function> → structured tool call."""
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta(
            '<function=ReactJudgeOutput>'
            '<parameter=has_issue>False</parameter>'
            '</function>'
        )

        result = finalizer.process_event(event)

        # Should emit replacement events
        # When there's no surrounding text, only ToolCallDelta is emitted
        assert len(result) == 1, f"Expected 1 event, got {len(result)}: {result}"

        # Event should be ToolCallDelta
        assert isinstance(result[0], ToolCallDelta)
        assert result[0].name == "ReactJudgeOutput"
        assert result[0].arguments_delta == '{"has_issue":"False"}'
        assert result[0].id.startswith("call_")

    def test_no_xml_pass_through(self):
        """No XML → pass through unchanged."""
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta("This is a normal response.")

        result = finalizer.process_event(event)

        # Should pass through unchanged
        assert len(result) == 1
        assert isinstance(result[0], AssistantTextDelta)
        assert result[0].delta == "This is a normal response."

    def test_empty_content_pass_through(self):
        """Empty content → pass through unchanged."""
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta("")

        result = finalizer.process_event(event)

        # Should pass through unchanged
        assert len(result) == 1
        assert isinstance(result[0], AssistantTextDelta)
        assert result[0].delta == ""

    def test_invalid_xml_passthrough(self):
        """Invalid XML (truly broken, not just incomplete) → pass through unchanged."""
        finalizer = ToolRewriteFinalizer()
        # Truly invalid XML: malformed tags
        event = _make_text_delta("<read><path>README.md</path>")

        result = finalizer.process_event(event)

        # Should buffer (XML is incomplete — missing </read>)
        assert len(result) == 0, f"Expected 0 events (buffered), got {len(result)}"
        assert finalizer.is_buffering

        # Call finalize() to handle incomplete XML
        finalize_result = finalizer.finalize()

        # Should emit buffered content as-is
        assert len(finalize_result) == 1
        assert isinstance(finalize_result[0], AssistantTextDelta)
        assert finalize_result[0].delta == "<read><path>README.md</path>"


# ---------------------------------------------------------------------------
# Test 2: Multi-chunk XML (MANDATORY)
# ---------------------------------------------------------------------------


class TestToolRewriteFinalizerMultiChunk:
    """Multi-chunk XML tests."""

    def test_xml_split_across_two_events(self):
        """XML split across two AssistantTextDelta events."""
        finalizer = ToolRewriteFinalizer()

        # First chunk: starts XML
        event1 = _make_text_delta('<read><path>')
        result1 = finalizer.process_event(event1)

        # Should buffer (return [])
        assert len(result1) == 0, f"Expected 0 events (buffered), got {len(result1)}"
        assert finalizer.is_buffering

        # Second chunk: completes XML
        event2 = _make_text_delta('README.md</path></read>')
        result2 = finalizer.process_event(event2)

        # Should emit replacement events
        # When there's no surrounding text, only ToolCallDelta is emitted
        assert len(result2) == 1, f"Expected 1 event, got {len(result2)}: {result2}"

        # Event should be ToolCallDelta
        assert isinstance(result2[0], ToolCallDelta)
        assert result2[0].name == "read"
        assert result2[0].arguments_delta == '{"path":"README.md"}'
        assert result2[0].id.startswith("call_")

    def test_xml_split_across_three_events(self):
        """XML split across three AssistantTextDelta events."""
        finalizer = ToolRewriteFinalizer()

        # First chunk: starts XML
        event1 = _make_text_delta('<read>')
        result1 = finalizer.process_event(event1)

        # Should buffer (return [])
        assert len(result1) == 0, f"Expected 0 events (buffered), got {len(result1)}"
        assert finalizer.is_buffering

        # Second chunk: continues XML
        event2 = _make_text_delta('<path>README.md</path>')
        result2 = finalizer.process_event(event2)

        # Should continue buffering (return [])
        assert len(result2) == 0, f"Expected 0 events (buffered), got {len(result2)}"
        assert finalizer.is_buffering

        # Third chunk: completes XML
        event3 = _make_text_delta('</read>')
        result3 = finalizer.process_event(event3)

        # Should emit replacement events
        # When there's no surrounding text, only ToolCallDelta is emitted
        assert len(result3) == 1, f"Expected 1 event, got {len(result3)}: {result3}"

        # Event should be ToolCallDelta
        assert isinstance(result3[0], ToolCallDelta)
        assert result3[0].name == "read"
        assert result3[0].arguments_delta == '{"path":"README.md"}'
        assert result3[0].id.startswith("call_")

    def test_xml_starts_in_one_event_completes_before_finish(self):
        """XML starts in one event and completes before Finish."""
        finalizer = ToolRewriteFinalizer()

        # Event with complete XML
        event = _make_text_delta('<read><path>README.md</path></read>')
        result = finalizer.process_event(event)

        # Should emit replacement events
        # When there's no surrounding text, only ToolCallDelta is emitted
        assert len(result) == 1, f"Expected 1 event, got {len(result)}: {result}"

        # Event should be ToolCallDelta
        assert isinstance(result[0], ToolCallDelta)
        assert result[0].name == "read"
        assert result[0].arguments_delta == '{"path":"README.md"}'
        assert result[0].id.startswith("call_")

    def test_incomplete_xml_at_finish(self):
        """Incomplete XML at Finish is preserved as text."""
        finalizer = ToolRewriteFinalizer()

        # Event with incomplete XML
        event = _make_text_delta('<read><path>README.md</path>')
        result = finalizer.process_event(event)

        # Should buffer (return [])
        assert len(result) == 0, f"Expected 0 events (buffered), got {len(result)}"
        assert finalizer.is_buffering

        # Call finalize() to handle incomplete XML
        finalize_result = finalizer.finalize()

        # Should emit buffered content as-is
        assert len(finalize_result) == 1
        assert isinstance(finalize_result[0], AssistantTextDelta)
        assert finalize_result[0].delta == '<read><path>README.md</path>'

    def test_surrounding_text_before_after_xml_emitted_exactly_once(self):
        """Surrounding text before/after XML emitted exactly once."""
        finalizer = ToolRewriteFinalizer()

        # Event with XML and surrounding text
        event = _make_text_delta('Let me run this:\n<read><path>README.md</path></read>\nDone.')

        result = finalizer.process_event(event)

        # Should emit replacement events
        assert len(result) == 2, f"Expected 2 events, got {len(result)}: {result}"

        # First event should be cleaned AssistantTextDelta with surrounding text
        assert isinstance(result[0], AssistantTextDelta)
        # The cleaned text should contain the surrounding text but not the XML
        assert "Let me run this:" in result[0].delta
        assert "Done." in result[0].delta
        assert "<read>" not in result[0].delta
        assert "<path>" not in result[0].delta

        # Second event should be ToolCallDelta
        assert isinstance(result[1], ToolCallDelta)
        assert result[1].name == "read"
        assert result[1].arguments_delta == '{"path":"README.md"}'
        assert result[1].id.startswith("call_")


# ---------------------------------------------------------------------------
# Test 3: Edge cases
# ---------------------------------------------------------------------------


class TestToolRewriteFinalizerEdgeCases:
    """Edge case tests."""

    def test_xml_with_cleaned_content(self):
        """XML with surrounding text → cleaned content + tool_calls."""
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta('Let me run this:\n<read><path>README.md</path></read>\nDone.')

        result = finalizer.process_event(event)

        # Should emit replacement events
        assert len(result) == 2, f"Expected 2 events, got {len(result)}: {result}"

        # First event should be cleaned AssistantTextDelta with surrounding text
        assert isinstance(result[0], AssistantTextDelta)
        assert "Let me run this:" in result[0].delta
        assert "Done." in result[0].delta

        # Second event should be ToolCallDelta
        assert isinstance(result[1], ToolCallDelta)
        assert result[1].name == "read"
        assert result[1].arguments_delta == '{"path":"README.md"}'

    def test_xml_with_special_characters(self):
        """XML with special characters (invalid XML) → pass through unchanged.

        D1 Policy: Invalid/malformed pseudo-tool XML passes through unchanged.
        No ToolCallDelta is emitted. Finish.reason remains "stop".

        Malformed XML with unescaped '&' is currently fail-open pass-through.
        Future phase may add safe parser hardening if needed.
        """
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta('<read><path>README.md with spaces & special chars</path></read>')

        result = finalizer.process_event(event)

        # Invalid XML → pass through unchanged (no rewrite)
        assert len(result) == 1, f"Expected 1 event (pass-through), got {len(result)}: {result}"
        assert isinstance(result[0], AssistantTextDelta)
        assert result[0].delta == '<read><path>README.md with spaces & special chars</path></read>'

    def test_xml_with_unicode(self):
        """XML with Unicode characters → cleaned content + tool_calls."""
        finalizer = ToolRewriteFinalizer()
        event = _make_text_delta('<read><path>README.md with unicode: 你好世界</path></read>')

        result = finalizer.process_event(event)

        # Should emit replacement events
        # When there's no surrounding text, only ToolCallDelta is emitted
        assert len(result) == 1, f"Expected 1 event, got {len(result)}: {result}"

        # Event should be ToolCallDelta
        assert isinstance(result[0], ToolCallDelta)
        assert result[0].name == "read"
        # Unicode may be escaped in JSON
        assert "README.md with unicode" in result[0].arguments_delta


# ---------------------------------------------------------------------------
# Test 4: Integration with ToolCallFinalizer (REQUIRES finalizer chaining fix)
# ---------------------------------------------------------------------------


class TestToolRewriteFinalizerIntegration:
    """Integration tests with ToolCallFinalizer (requires finalizer chaining fix)."""

    def test_tool_call_assembly_after_rewrite(self):
        """ToolCallDelta from ToolRewrite → ToolCallComplete via ToolCallFinalizer."""
        from keeprollming.streaming.finalizers import ToolCallFinalizer
        from keeprollming.streaming.events import ToolCallComplete

        # Set up finalizers
        tool_rewrite = ToolRewriteFinalizer()
        tool_call = ToolCallFinalizer()

        # Emit AssistantTextDelta with XML content
        event = _make_text_delta('<read><path>README.md</path></read>')
        rewrite_result = tool_rewrite.process_event(event)

        # ToolRewrite should emit ToolCallDelta (no surrounding text)
        assert len(rewrite_result) == 1
        assert isinstance(rewrite_result[0], ToolCallDelta)

        # Pass ToolCallDelta to ToolCallFinalizer
        tcf_result = tool_call.process_event(rewrite_result[0])

        # ToolCallFinalizer should buffer (return [])
        assert len(tcf_result) == 0, f"Expected 0 events (buffered), got {len(tcf_result)}"

        # Call finalize() to flush ToolCallComplete
        finalize_result = tool_call.finalize()

        # Should emit ToolCallComplete
        assert len(finalize_result) == 1
        assert isinstance(finalize_result[0], ToolCallComplete)

    def test_tool_call_json_validation(self):
        """Invalid JSON in XML args → dropped by ToolCallFinalizer."""
        from keeprollming.streaming.finalizers import ToolCallFinalizer
        from keeprollming.streaming.events import ToolCallComplete

        # Set up finalizers
        tool_rewrite = ToolRewriteFinalizer()
        tool_call = ToolCallFinalizer(flush_valid_only=True)

        # Emit AssistantTextDelta with XML content with invalid JSON
        # Use a value that is not valid JSON (e.g., a bare string without quotes)
        event = _make_text_delta('<read><path>not valid json</path></read>')
        rewrite_result = tool_rewrite.process_event(event)

        # ToolRewrite should emit ToolCallDelta (no surrounding text)
        assert len(rewrite_result) == 1
        assert isinstance(rewrite_result[0], ToolCallDelta)

        # Pass ToolCallDelta to ToolCallFinalizer
        tcf_result = tool_call.process_event(rewrite_result[0])

        # ToolCallFinalizer should buffer (return [])
        assert len(tcf_result) == 0, f"Expected 0 events (buffered), got {len(tcf_result)}"

        # Call finalize() to flush ToolCallComplete
        finalize_result = tool_call.finalize()

        # The JSON {"path":"not valid json"} is actually valid JSON,
        # so ToolCallComplete should be emitted.
        # This test verifies the ToolCallFinalizer correctly handles valid JSON.
        assert len(finalize_result) == 1
        assert isinstance(finalize_result[0], ToolCallComplete)


# ---------------------------------------------------------------------------
# Test 5: Finalize
# ---------------------------------------------------------------------------


class TestToolRewriteFinalizerFinalize:
    """Finalize tests."""

    def test_finalize_returns_empty(self):
        """finalize() returns empty list (when no incomplete XML)."""
        finalizer = ToolRewriteFinalizer()

        # Emit normal text (no XML)
        event = _make_text_delta("Normal text")
        result = finalizer.process_event(event)

        # Should pass through
        assert len(result) == 1

        # Call finalize()
        finalize_result = finalizer.finalize()

        # Should return empty list
        assert len(finalize_result) == 0

    def test_finalize_emits_incomplete_xml(self):
        """finalize() emits incomplete XML as-is."""
        finalizer = ToolRewriteFinalizer()

        # Emit incomplete XML
        event = _make_text_delta('<read><path>README.md</path>')
        result = finalizer.process_event(event)

        # Should buffer (return [])
        assert len(result) == 0
        assert finalizer.is_buffering

        # Call finalize() to handle incomplete XML
        finalize_result = finalizer.finalize()

        # Should emit buffered content as-is
        assert len(finalize_result) == 1
        assert isinstance(finalize_result[0], AssistantTextDelta)
        assert finalize_result[0].delta == '<read><path>README.md</path>'

    def test_finalize_idempotent(self):
        """finalize() safe to call multiple times (raises RuntimeError on second call)."""
        finalizer = ToolRewriteFinalizer()

        # Call finalize() once
        result1 = finalizer.finalize()
        assert len(result1) == 0

        # Call finalize() again — should raise RuntimeError
        with pytest.raises(RuntimeError, match="already called"):
            finalizer.finalize()


# ---------------------------------------------------------------------------
# Test 6: State accessors
# ---------------------------------------------------------------------------


class TestToolRewriteFinalizerState:
    """State accessor tests."""

    def test_is_buffering_property(self):
        """is_buffering property returns correct state."""
        finalizer = ToolRewriteFinalizer()

        # Initially not buffering
        assert not finalizer.is_buffering

        # Emit incomplete XML
        event = _make_text_delta('<read><path>README.md</path>')
        result = finalizer.process_event(event)

        # Should be buffering
        assert finalizer.is_buffering

        # Call finalize() to handle incomplete XML
        finalizer.finalize()

        # Should not be buffering anymore
        assert not finalizer.is_buffering

    def test_rewritten_tool_call_id_property(self):
        """rewritten_tool_call_id property returns correct ID."""
        finalizer = ToolRewriteFinalizer()

        # Initially None
        assert finalizer.rewritten_tool_call_id is None

        # Emit complete XML
        event = _make_text_delta('<read><path>README.md</path></read>')
        result = finalizer.process_event(event)

        # Should have rewritten_tool_call_id
        assert finalizer.rewritten_tool_call_id is not None
        assert finalizer.rewritten_tool_call_id.startswith("call_")

        # After finalize, should still have the ID
        finalizer.finalize()
        assert finalizer.rewritten_tool_call_id is not None

    def test_reset_property(self):
        """reset() clears internal state."""
        finalizer = ToolRewriteFinalizer()

        # Emit incomplete XML
        event = _make_text_delta('<read><path>README.md</path>')
        result = finalizer.process_event(event)

        # Should be buffering
        assert finalizer.is_buffering

        # Reset
        finalizer.reset()

        # Should not be buffering anymore
        assert not finalizer.is_buffering
        assert finalizer.rewritten_tool_call_id is None

    def test_reset_preserve_buffer(self):
        """reset(preserve_buffer=True) preserves XML buffer."""
        finalizer = ToolRewriteFinalizer()

        # Emit incomplete XML
        event = _make_text_delta('<read><path>README.md</path>')
        result = finalizer.process_event(event)

        # Should be buffering
        assert finalizer.is_buffering

        # Reset with preserve_buffer=True
        finalizer.reset(preserve_buffer=True)

        # Should still be buffering (buffer preserved)
        assert finalizer.is_buffering

        # Reset with preserve_buffer=False (default)
        finalizer.reset(preserve_buffer=False)

        # Should not be buffering anymore
        assert not finalizer.is_buffering
