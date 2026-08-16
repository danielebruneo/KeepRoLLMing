"""Unit tests for the timestamp footer strip/replace logic in TimestampFilter.

Tests verify that the template-derived strip regex correctly:
- Strips a stale timestamp footer at the end of content
- Strips duplicate stale+fresh footers down to one
- Preserves timestamp-looking text in the middle of content
- Does NOT strip footers from a different template
- Works with both the default and custom timestamp templates
"""

import pytest

from keeprollming.filters.timestamp.request import TimestampFilter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_filter():
    """TimestampFilter with the default template."""
    return TimestampFilter(config={"template": "\n\n---\nTimestamp: %Y-%m-%d %H:%M:%S UTC"})


@pytest.fixture
def custom_bracket_filter():
    """TimestampFilter with a custom bracket-style template."""
    return TimestampFilter(config={"template": "[Generated at %Y-%m-%d]"})


@pytest.fixture
def custom_hash_filter():
    """TimestampFilter with a custom hash-style template."""
    return TimestampFilter(config={"template": "### Response time: %Y-%m-%d %H:%M:%S"})


# ---------------------------------------------------------------------------
# Tests: default template
# ---------------------------------------------------------------------------

class TestDefaultTemplate:
    """Tests using the default timestamp template."""

    def test_single_stale_footer_stripped(self, default_filter):
        """A single stale footer at the end is stripped."""
        content = "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC"
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_double_footer_strips_to_base(self, default_filter):
        """Duplicate stale + fresh footer strips ALL consecutive footers to base."""
        content = (
            "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC\n\n"
            "---\nTimestamp: 2026-06-28 18:55:58 UTC"
        )
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_middle_timestamp_preserved(self, default_filter):
        """Timestamp-looking text in the middle is preserved."""
        content = "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC\nMore text"
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC\nMore text"

    def test_middle_and_end_footers(self, default_filter):
        """Middle footer preserved, end footer stripped."""
        content = (
            "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC\nMore text\n\n"
            "---\nTimestamp: 2026-06-28 18:55:58 UTC"
        )
        result = default_filter._strip_existing_timestamp_footer(content)
        assert "Timestamp: 2020-01-01" in result
        assert "Timestamp: 2026-06-28" not in result

    def test_no_footer_unchanged(self, default_filter):
        """Content without a footer is unchanged."""
        content = "Hello world"
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello world"

    def test_empty_content_unchanged(self, default_filter):
        """Empty content is unchanged."""
        result = default_filter._strip_existing_timestamp_footer("")
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: custom bracket template
# ---------------------------------------------------------------------------

class TestCustomBracketTemplate:
    """Tests using a custom [Generated at %Y-%m-%d] template."""

    def test_single_stale_footer_stripped(self, custom_bracket_filter):
        """A single stale footer at the end is stripped."""
        content = "Hello\n[Generated at 2020-01-01]"
        result = custom_bracket_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_double_footer_strips_to_base(self, custom_bracket_filter):
        """Duplicate stale + fresh footer strips ALL consecutive footers to base."""
        content = (
            "Hello\n[Generated at 2020-01-01]\n[Generated at 2026-06-28]"
        )
        result = custom_bracket_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_middle_timestamp_preserved(self, custom_bracket_filter):
        """Timestamp-looking text in the middle is preserved."""
        content = "Hello\n[Generated at 2020-01-01]\nMore text"
        result = custom_bracket_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello\n[Generated at 2020-01-01]\nMore text"

    def test_middle_and_end_footers(self, custom_bracket_filter):
        """Middle footer preserved, end footer stripped."""
        content = (
            "Hello\n[Generated at 2020-01-01]\nMore text\n[Generated at 2026-06-28]"
        )
        result = custom_bracket_filter._strip_existing_timestamp_footer(content)
        assert "Generated at 2020-01-01" in result
        assert "Generated at 2026-06-28" not in result

    def test_different_template_not_stripped(self, custom_bracket_filter):
        """A footer from a different template is not stripped."""
        content = "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC"
        result = custom_bracket_filter._strip_existing_timestamp_footer(content)
        assert result == content

    def test_three_consecutive_footers_stripped(self, custom_bracket_filter):
        """Three consecutive bracket footers should all be stripped to base content."""
        content = (
            "Hello\n[Generated at 2020-01-01]\n"
            "[Generated at 2025-01-01]\n"
            "[Generated at 2026-01-01]"
        )
        result = custom_bracket_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_trailing_whitespace_after_footer_stripped(self, custom_bracket_filter):
        """Stale bracket footer followed by trailing whitespace should still be stripped."""
        content = "Hello\n[Generated at 2020-01-01]   \n  "
        result = custom_bracket_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"


# ---------------------------------------------------------------------------
# Tests: custom hash template
# ---------------------------------------------------------------------------

class TestCustomHashTemplate:
    """Tests using a custom ### Response time: %Y-%m-%d %H:%M:%S template."""

    def test_single_stale_footer_stripped(self, custom_hash_filter):
        """A single stale footer at the end is stripped."""
        content = "Hello\n### Response time: 2020-01-01 00:00:00"
        result = custom_hash_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_double_footer_strips_to_base(self, custom_hash_filter):
        """Duplicate stale + fresh footer strips ALL consecutive footers to base."""
        content = (
            "Hello\n### Response time: 2020-01-01 00:00:00\n"
            "### Response time: 2026-06-28 18:55:58"
        )
        result = custom_hash_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_middle_timestamp_preserved(self, custom_hash_filter):
        """Timestamp-looking text in the middle is preserved."""
        content = "Hello\n### Response time: 2020-01-01 00:00:00\nMore text"
        result = custom_hash_filter._strip_existing_timestamp_footer(content)
        assert result == content

    def test_different_template_not_stripped(self, custom_hash_filter):
        """A footer from a different template is not stripped."""
        content = "Hello\n[Generated at 2020-01-01]"
        result = custom_hash_filter._strip_existing_timestamp_footer(content)
        assert result == content

    def test_three_consecutive_footers_stripped(self, custom_hash_filter):
        """Three consecutive hash footers should all be stripped to base content."""
        content = (
            "Hello\n### Response time: 2020-01-01 00:00:00\n"
            "### Response time: 2025-01-01 00:00:00\n"
            "### Response time: 2026-01-01 00:00:00"
        )
        result = custom_hash_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_trailing_whitespace_after_footer_stripped(self, custom_hash_filter):
        """Stale hash footer followed by trailing whitespace should still be stripped."""
        content = "Hello\n### Response time: 2020-01-01 00:00:00   \n  "
        result = custom_hash_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"


# ---------------------------------------------------------------------------
# Tests: _content_has_timestamp_footer
# ---------------------------------------------------------------------------

class TestContentHasTimestampFooter:
    """Tests for the _content_has_timestamp_footer detection method."""

    def test_detects_default_footer(self, default_filter):
        assert default_filter._content_has_timestamp_footer(
            "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC"
        )
        assert default_filter._content_has_timestamp_footer(
            "Hello\n\n---\nTimestamp: 2026-06-28 18:55:58 UTC"
        )

    def test_detects_custom_bracket_footer(self, custom_bracket_filter):
        assert custom_bracket_filter._content_has_timestamp_footer(
            "Hello\n[Generated at 2020-01-01]"
        )

    def test_detects_custom_hash_footer(self, custom_hash_filter):
        assert custom_hash_filter._content_has_timestamp_footer(
            "Hello\n### Response time: 2020-01-01 00:00:00"
        )

    def test_rejects_non_matching_footer(self, default_filter):
        assert not default_filter._content_has_timestamp_footer(
            "Hello\n### Response time: 2020-01-01 00:00:00"
        )

    def test_rejects_no_footer(self, default_filter):
        assert not default_filter._content_has_timestamp_footer("Hello world")

    def test_rejects_empty(self, default_filter):
        assert not default_filter._content_has_timestamp_footer("")

    def test_trailing_whitespace_after_footer_stripped(self, default_filter):
        """Stale footer followed by trailing spaces/newlines/tabs should still be stripped."""
        content = "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC   \n  \t  "
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_trailing_newline_after_footer_stripped(self, default_filter):
        """Stale footer followed by trailing newlines should still be stripped."""
        content = "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC\n  "
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_three_consecutive_footers_stripped(self, default_filter):
        """Three consecutive final timestamp footers should all be stripped to base content."""
        content = (
            "Hello\n\n---\nTimestamp: 2020-01-01 00:00:00 UTC\n\n"
            "---\nTimestamp: 2025-01-01 00:00:00 UTC\n\n"
            "---\nTimestamp: 2026-01-01 00:00:00 UTC"
        )
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello"

    def test_middle_timestamp_preserved_trailing_ws(self, default_filter):
        """Timestamp-looking text in the middle followed by more normal text must still be preserved."""
        content = "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC\nMore text here"
        result = default_filter._strip_existing_timestamp_footer(content)
        assert result == "Hello\n---\nTimestamp: 2020-01-01 00:00:00 UTC\nMore text here"
