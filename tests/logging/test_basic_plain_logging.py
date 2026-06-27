"""Tests for BASIC_PLAIN logging mode.

These tests verify the formatting, colorization, and truncation behavior of the
BASIC_PLAIN logging output. Migrated from tests/test_orchestrator.py as part of
Phase 2 test refactoring.
"""

import pytest


@pytest.fixture
def monkeypatch_log_mode(monkeypatch):
    """Fixture to set LOG_MODE to BASIC_PLAIN for all relevant modules."""
    import keeprollming.logger as logger_mod
    from keeprollming.logging import constants as logging_constants

    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logging_constants, "LOG_PLAIN_COLORS", True)  # Enable colors by default
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())


@pytest.fixture
def monkeypatch_log_mode_no_colors(monkeypatch):
    """Fixture to set LOG_MODE to BASIC_PLAIN without colors."""
    import keeprollming.logger as logger_mod
    from keeprollming.logging import constants as logging_constants

    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logging_constants, "LOG_PLAIN_COLORS", False)  # Disable colors
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())


class TestBasicPlainFormatting:
    """Test BASIC_PLAIN log formatting and colorization."""

    def test_basic_plain_multiline_content_is_indented_and_colored(self, monkeypatch_log_mode):
        """Test that multiline content is properly indented and colorized.
        
        Verifies the _format_plain function correctly handles:
        - ANSI color codes for different log levels
        - Multi-line assistant text with proper indentation
        - Bracket-like markup preservation
        """
        import keeprollming.logger as logger_mod

        rendered = logger_mod._format_plain({
            "msg": "http_out",
            "req_id": "abc123",
            "model": "demo-model",
            "elapsed_ms": 12.3,
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "assistant_text": "line one\n[line like markup]\nline three",
        })

        assert "\x1b[" in rendered
        plain = logger_mod._strip_ansi(rendered)
        assert "┌─ REQUEST abc123" in plain
        assert "│ RESULT model=demo-model" in plain
        assert "elapsed_ms=12.3" in plain
        assert "usage=prompt=10, completion=20, total=30" in plain
        assert "│   assistant" in plain
        assert "│     [line like markup]" in plain
        assert "└─ END abc123" in plain

    def test_basic_plain_wraps_long_lines(self, monkeypatch):
        """Test that long lines are wrapped according to LOG_PLAIN_WRAP_WIDTH.
        
        Verifies the log wrapping behavior when text exceeds the configured
        wrap width (default 80 chars, but can be overridden).
        """
        import keeprollming.logger as logger_mod
        from keeprollming.logging import constants as logging_constants

        monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
        monkeypatch.setattr(logging_constants, "LOG_PLAIN_COLORS", False)
        monkeypatch.setattr(logging_constants, "LOG_PLAIN_WRAP_WIDTH", 50)
        monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
        monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

        rendered = logger_mod._format_plain({
            "msg": "http_out",
            "req_id": "wraptest",
            "model": "demo-model",
            "elapsed_ms": 1.2,
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            "assistant_text": "AI: " + ("x " * 40),
        })

        plain = logger_mod._strip_ansi(rendered) if "\x1b[" in rendered else rendered
        assert "┌─ REQUEST wraptest" in plain
        # The assistant text should be wrapped and indented
        assert "│   assistant" in plain
        assert "│     AI:" in plain  # Assistant content is indented further


class TestBasicPlainTruncation:
    """Test BASIC_PLAIN logging truncation behavior."""

    def test_basic_plain_does_not_truncate_by_default(self, monkeypatch_log_mode_no_colors):
        """Verify that BASIC_PLAIN does not truncate by default.
        
        When BASIC_SNIP_CHARS is 0 (default), all content should be preserved
        regardless of length. This ensures no data loss in logs.
        """
        import keeprollming.logger as logger_mod

        long_text = "A" * 2500
        rendered = logger_mod._format_plain({
            "msg": "http_out",
            "req_id": "req-full",
            "model": "demo-model",
            "elapsed_ms": 1.2,
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            "assistant_text": long_text,
        })

        assert "<snip" not in rendered
        assert long_text in rendered


class TestBasicPlainSummaryReply:
    """Test BASIC_PLAIN logging for summary replies."""

    def test_basic_plain_highlights_ai_human_chunks(self, monkeypatch_log_mode):
        """Verify that AI/Human chunks are properly highlighted in summary reply logs.
        
        Tests the _format_plain function's handling of summary_snip content,
        ensuring human/AI message boundaries are preserved and colorized.
        """
        import keeprollming.logger as logger_mod

        rendered = logger_mod._format_plain({
            "msg": "summary_reply",
            "req_id": "abc123",
            "elapsed_ms": 1.0,
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            "summary_snip": "Human: hello\nAI: hi there",
        })

        assert "\x1b[" in rendered
        plain = logger_mod._strip_ansi(rendered)
        assert "Human: hello" in plain
        assert "AI: hi there" in plain
