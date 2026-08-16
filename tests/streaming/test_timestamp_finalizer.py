"""Unit tests for TimestampFinalizer — V2 tail-buffer finalizer.

Tests verify:
- Tail-buffer emits safe prefix when buffer exceeds size
- Stale timestamp footer is stripped and replaced with one fresh footer
- Duplicate footers collapse to one
- Middle timestamp text is preserved
- Custom templates (bracket, hash) work correctly
- Different-template footers are not stripped
- Chunking invariance
- Finalize idempotence
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from keeprollming.streaming.events import AssistantTextDelta
from keeprollming.filters.timestamp.stream import (
    TimestampFinalizer,
    _strip_existing_timestamp_footer,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATE = "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"
_BRACKET_TEMPLATE = "[Generated at %Y-%m-%d]"
_HASH_TEMPLATE = "### Response time: %Y-%m-%d %H:%M:%S"

# Fixed clock for deterministic tests
_FIXED_DT = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)


def _clock() -> datetime:
    return _FIXED_DT


def _fresh_default_footer() -> str:
    return "\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC"


def _fresh_bracket_footer() -> str:
    return "[Generated at 2026-06-29]"


def _fresh_hash_footer() -> str:
    return "### Response time: 2026-06-29 12:00:00"


# ---------------------------------------------------------------------------
# Test 1: short_response_appends_timestamp
# ---------------------------------------------------------------------------


def test_short_response_appends_timestamp():
    """Input shorter than tail buffer: process_delta emits nothing, finalize emits content + fresh footer."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    # Short content — stays in tail buffer
    deltas = finalizer.process_delta("Hello")
    assert deltas == [], "Short content should stay in tail buffer"

    final_list = finalizer.finalize()
    assert len(final_list) == 1
    final = final_list[0]
    assert isinstance(final, AssistantTextDelta)

    text = final.delta
    assert "Hello" in text
    assert "2026-06-29 12:00:00 UTC" in text
    assert text.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")

    # Exactly one footer
    footer_count = text.count("---\nTimestamp: 2026-06-29 12:00:00 UTC")
    assert footer_count == 1


def test_empty_response_does_not_produce_timestamp_only_delta():
    """Tool-call turns have no assistant text to timestamp."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )
    assert finalizer.finalize() == []


# ---------------------------------------------------------------------------
# Test 2: stale_footer_replaced
# ---------------------------------------------------------------------------


def test_stale_footer_replaced():
    """Stale timestamp footer at end is stripped, replaced by one fresh footer."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    finalizer.process_delta("Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC")
    final_list = finalizer.finalize()
    text = final_list[0].delta
    assert text.startswith("Hello")
    # Stale footer gone
    assert "Timestamp: 2020-01-01" not in text
    # Fresh footer present
    assert "Timestamp: 2026-06-29 12:00:00 UTC" in text
    assert text.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")


# ---------------------------------------------------------------------------
# Test 3: duplicate_final_footers_collapse_to_one
# ---------------------------------------------------------------------------


def test_duplicate_final_footers_collapse_to_one():
    """Two consecutive final timestamp footers collapse to base + one fresh footer."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    content = (
        "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC\n\n"
        "---\nTimestamp: 2026-06-28 18:55:58 UTC"
    )
    finalizer.process_delta(content)
    final_list = finalizer.finalize()
    text = final_list[0].delta
    assert text.startswith("Hello")
    # Both stale footers gone
    assert "Timestamp: 2020-01-01" not in text
    assert "Timestamp: 2026-06-28 18:55:58" not in text
    # Exactly one fresh footer
    assert "Timestamp: 2026-06-29 12:00:00 UTC" in text
    assert text.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")


# ---------------------------------------------------------------------------
# Test 4: middle_timestamp_preserved
# ---------------------------------------------------------------------------


def test_middle_timestamp_preserved():
    """Timestamp-looking text in the middle plus normal trailing text: middle preserved, fresh footer appended."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    content = "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC\nMore text"
    finalizer.process_delta(content)
    final_list = finalizer.finalize()
    text = final_list[0].delta
    assert "Timestamp: 2020-01-01 00:00:00 UTC" in text
    assert "More text" in text
    assert text.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")


# ---------------------------------------------------------------------------
# Test 5: custom_template_bracket
# ---------------------------------------------------------------------------


def test_custom_template_bracket():
    """Custom bracket template: stale bracket footer replaced by one fresh bracket footer."""
    finalizer = TimestampFinalizer(
        template=_BRACKET_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    content = "Hello\n[Generated at 2020-01-01]"
    finalizer.process_delta(content)
    final_list = finalizer.finalize()
    text = final_list[0].delta
    assert text.startswith("Hello")
    assert "Generated at 2020-01-01" not in text
    assert "Generated at 2026-06-29" in text
    assert text.endswith("[Generated at 2026-06-29]")


# ---------------------------------------------------------------------------
# Test 6: custom_template_hash
# ---------------------------------------------------------------------------


def test_custom_template_hash():
    """Custom hash template: stale hash footer replaced by one fresh hash footer."""
    finalizer = TimestampFinalizer(
        template=_HASH_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    content = "Hello\n### Response time: 2020-01-01 00:00:00"
    finalizer.process_delta(content)
    final_list = finalizer.finalize()
    text = final_list[0].delta
    assert text.startswith("Hello")
    assert "Response time: 2020-01-01" not in text
    assert "Response time: 2026-06-29 12:00:00" in text
    assert text.endswith("### Response time: 2026-06-29 12:00:00")


# ---------------------------------------------------------------------------
# Test 7: different_template_not_stripped
# ---------------------------------------------------------------------------


def test_different_template_not_stripped():
    """Finalizer with bracket template: default Timestamp footer remains as normal text."""
    finalizer = TimestampFinalizer(
        template=_BRACKET_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    content = "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC"
    finalizer.process_delta(content)
    final_list = finalizer.finalize()
    text = final_list[0].delta
    # Different-template footer preserved
    assert "---\nTimestamp: 2020-01-01 00:00:00 UTC" in text
    # Fresh bracket footer appended
    assert "Generated at 2026-06-29" in text
    assert text.endswith("[Generated at 2026-06-29]")


# ---------------------------------------------------------------------------
# Test 8: long_response_emits_safe_prefix_before_finalize
# ---------------------------------------------------------------------------


def test_long_response_emits_safe_prefix_before_finalize():
    """Long response with small tail buffer: safe prefix emitted before finalize."""
    tail_size = 20
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=tail_size
    )

    long_content = "A" * 50  # 50 chars, well above tail buffer
    deltas = finalizer.process_delta(long_content)

    # Should emit safe prefix (50 - 20 = 30 chars)
    assert len(deltas) == 1
    emitted = deltas[0].delta
    assert len(emitted) == 30
    assert emitted == "A" * 30

    # Tail buffer should have the last 20 chars
    assert finalizer._tail_buffer == "A" * 20

    final_list = finalizer.finalize()
    final_text = final_list[0].delta

    # finalize() returns only the corrected tail (stripped tail + fresh footer).
    # Tail is "A"*20 with no footer → stripped stays "A"*20.
    expected_final = "A" * 20 + "\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC"
    assert final_text == expected_final

    # Verify no duplication/loss: concatenation of all emitted deltas + finalize
    all_parts = [d.delta for d in deltas] + [final_text]
    full_text = "".join(all_parts)
    expected_full = "A" * 50 + "\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC"
    assert full_text == expected_full
    # Should have the base text once, plus the fresh footer
    assert full_text.count("A" * 50) == 1
    assert full_text.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")


# ---------------------------------------------------------------------------
# Test 9: chunking_invariance
# ---------------------------------------------------------------------------


def test_chunking_invariance():
    """Same logical response fed as one delta vs many small deltas: equivalent output."""
    base_content = "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC"

    # Single delta approach
    final_single = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )
    final_single.process_delta(base_content)
    single_list = final_single.finalize()
    single_text = single_list[0].delta

    # Many small deltas approach
    final_multi = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )
    for ch in base_content:
        final_multi.process_delta(ch)
    multi_list = final_multi.finalize()
    multi_text = multi_list[0].delta

    # Strip the timestamp value to compare structural equivalence
    def _normalize(ts: str) -> str:
        """Remove the timestamp value portion for comparison."""
        return ts.replace("2026-06-29 12:00:00 UTC", "TIMESTAMP")

    assert _normalize(single_text) == _normalize(multi_text), (
        f"Single: {single_text!r}\nMulti: {multi_text!r}"
    )


# ---------------------------------------------------------------------------
# Test 10: finalize_idempotence
# ---------------------------------------------------------------------------


def test_finalize_idempotence():
    """Second finalize() raises RuntimeError (not idempotent by default)."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    finalizer.process_delta("Hello")
    final_list = finalizer.finalize()
    assert final_list[0].delta.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")

    with pytest.raises(RuntimeError, match="already called"):
        finalizer.finalize()


# ---------------------------------------------------------------------------
# Test 11: three_consecutive_footers_collapse_to_one
# ---------------------------------------------------------------------------


def test_three_consecutive_footers_collapse_to_one():
    """Three consecutive final timestamp footers collapse to base + one fresh footer."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    content = (
        "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC\n\n"
        "---\nTimestamp: 2025-01-01 00:00:00 UTC\n\n"
        "---\nTimestamp: 2026-01-01 00:00:00 UTC"
    )
    finalizer.process_delta(content)
    final_list = finalizer.finalize()
    text = final_list[0].delta
    assert text.startswith("Hello")
    # All stale footers gone
    assert "Timestamp: 2020-01-01" not in text
    assert "Timestamp: 2025-01-01" not in text
    assert "Timestamp: 2026-01-01" not in text
    # Exactly one fresh footer
    assert "Timestamp: 2026-06-29 12:00:00 UTC" in text
    assert text.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")


# ---------------------------------------------------------------------------
# Test 12: trailing_whitespace_after_stale_footer
# ---------------------------------------------------------------------------


def test_trailing_whitespace_after_stale_footer():
    """Stale footer followed by trailing spaces/newlines/tabs: still stripped."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    content = "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC   \n  \t  "
    finalizer.process_delta(content)
    final_list = finalizer.finalize()
    text = final_list[0].delta
    assert text.startswith("Hello")
    assert "Timestamp: 2020-01-01" not in text
    assert "Timestamp: 2026-06-29 12:00:00 UTC" in text
    assert text.endswith("\n\n---\nTimestamp: 2026-06-29 12:00:00 UTC")


# ---------------------------------------------------------------------------
# Test 13: process_delta_after_finalize_raises
# ---------------------------------------------------------------------------


def test_process_delta_after_finalize_raises():
    """process_delta() called after finalize() raises RuntimeError."""
    finalizer = TimestampFinalizer(
        template=_DEFAULT_TEMPLATE, clock=_clock, tail_buffer_size=1024
    )

    finalizer.process_delta("Hello")
    finalizer.finalize()

    with pytest.raises(RuntimeError, match="already called"):
        finalizer.process_delta(" world")
