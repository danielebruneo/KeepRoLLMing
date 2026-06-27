"""
Tests for Tool-Call Rewriting Module

Tests cover:
- PseudoToolCallParser for both nested and separate patterns
- ToolCallRewriter streaming chunk transformation
- ToolCallRewriter response body transformation
- Edge cases and error handling
"""

import json
import pytest
from keeprollming.tool_rewrite import (
    PseudoToolCall,
    PseudoToolCallParser,
    ToolCallRewriter,
)


class TestPseudoToolCallParser:
    """Tests for PseudoToolCallParser class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.parser = PseudoToolCallParser()

    def test_parse_nested_pattern(self):
        """Test parsing nested XML pattern."""
        text = "<read><path>README.md</path></read>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        assert result.arguments == {"path": "README.md"}

    def test_parse_nested_pattern_multiple_args(self):
        """Test parsing nested XML with multiple arguments."""
        text = "<exec><command>ls -la</command><dir>/home</dir></exec>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "exec"
        assert result.arguments == {"command": "ls -la", "dir": "/home"}

    def test_parse_separate_pattern(self):
        """Test parsing separate XML pattern."""
        text = "<name>read</name><path>README.md</path>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        assert result.arguments == {"path": "README.md"}

    def test_parse_separate_pattern_multiple_args(self):
        """Test parsing separate XML with multiple arguments."""
        text = "<name>exec</name><command>pwd</command><dir>/tmp</dir>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "exec"
        assert result.arguments == {"command": "pwd", "dir": "/tmp"}

    def test_parse_nested_with_whitespace(self):
        """Test parsing nested pattern with extra whitespace."""
        text = "  \n  <read>  \n    <path>README.md</path>\n  </read>  \n"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        assert result.arguments == {"path": "README.md"}

    def test_parse_non_xml_text(self):
        """Test that plain text returns None."""
        text = "This is just plain text"
        result = self.parser.parse(text)

        assert result is None

    def test_parse_empty_string(self):
        """Test that empty string returns None."""
        result = self.parser.parse("")

        assert result is None

    def test_parse_none(self):
        """Test that None returns None."""
        result = self.parser.parse(None)

        assert result is None

    def test_parse_malformed_xml(self):
        """Test that malformed XML returns None."""
        text = "<read><path>README.md</read>"  # Mismatched tags
        result = self.parser.parse(text)

        assert result is None

    def test_parse_only_name_tag(self):
        """Test that only name tag without args now returns valid tool call (zero args)."""
        text = "<name>read</name>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        assert result.arguments == {}

    def test_parse_nested_only_name_tag(self):
        """Test that nested with only name tag now returns valid tool call (zero args)."""
        text = "<read></read>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        assert result.arguments == {}

    def test_parse_both_patterns_same_time(self):
        """Test parsing when both patterns are present."""
        # Separate pattern should be tried first
        text = "<name>read</name><path>README.md</path>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"

    def test_parse_nested_pattern_case_insensitive(self):
        """Test parsing nested pattern with different case."""
        text = "<READ><PATH>README.md</PATH></READ>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "READ"

    def test_parse_separate_pattern_case_insensitive(self):
        """Test parsing separate pattern with different case."""
        text = "<NAME>read</NAME><PATH>README.md</PATH>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"


class TestToolCallRewriter:
    """Tests for ToolCallRewriter class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.rewriter = ToolCallRewriter(enabled=True, request_id="test123")
        self.route_config_enabled = {"tool_rewrite_enabled": True}
        self.route_config_disabled = {"tool_rewrite_enabled": False}

    def test_should_rewrite_enabled_route(self):
        """Test should_rewrite returns True for enabled route."""
        assert self.rewriter.should_rewrite(self.route_config_enabled) is True

    def test_should_rewrite_disabled_route(self):
        """Test should_rewrite returns False for disabled route."""
        assert self.rewriter.should_rewrite(self.route_config_disabled) is False

    def test_should_rewrite_global_disabled(self):
        """Test should_rewrite returns False when rewriter is globally disabled."""
        disabled_rewriter = ToolCallRewriter(enabled=False)
        assert disabled_rewriter.should_rewrite(self.route_config_enabled) is False

    def test_rewrite_streaming_chunk_nested_pattern(self):
        """Test rewriting streaming chunk with nested pattern."""
        chunk_data = {
            "id": "test-123",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "<read><path>README.md</path></read>",
                    },
                    "finish_reason": None,
                }
            ],
        }
        chunk = (
            b"data: "
            + json.dumps(chunk_data, separators=(",", ":")).encode()
            + b"\n\n"
        )

        result = self.rewriter.rewrite_streaming_chunk(
            chunk, self.route_config_enabled
        )

        assert result is not None
        result_str = result.decode("utf-8")
        assert result_str.startswith("data: ")

        result_obj = json.loads(result_str[6:])
        assert "choices" in result_obj
        assert len(result_obj["choices"]) > 0

        delta = result_obj["choices"][0]["delta"]
        assert "tool_calls" in delta
        assert "content" not in delta
        assert delta["tool_calls"][0]["function"]["name"] == "read"
        assert delta["tool_calls"][0]["function"]["arguments"] == '{"path":"README.md"}'

    def test_rewrite_streaming_chunk_separate_pattern(self):
        """Test rewriting streaming chunk with separate pattern."""
        chunk_data = {
            "id": "test-123",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "<name>exec</name><command>pwd</command>",
                    },
                    "finish_reason": None,
                }
            ],
        }
        chunk = (
            b"data: "
            + json.dumps(chunk_data, separators=(",", ":")).encode()
            + b"\n\n"
        )

        result = self.rewriter.rewrite_streaming_chunk(
            chunk, self.route_config_enabled
        )

        assert result is not None
        result_str = result.decode("utf-8")
        result_obj = json.loads(result_str[6:])

        delta = result_obj["choices"][0]["delta"]
        assert "tool_calls" in delta
        assert delta["tool_calls"][0]["function"]["name"] == "exec"
        assert delta["tool_calls"][0]["function"]["arguments"] == '{"command":"pwd"}'

    def test_rewrite_streaming_chunk_no_rewrite_needed(self):
        """Test that chunks without pseudo-tool-call are returned unchanged."""
        chunk_data = {
            "id": "test-123",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "This is regular text content",
                    },
                    "finish_reason": None,
                }
            ],
        }
        chunk = (
            b"data: "
            + json.dumps(chunk_data, separators=(",", ":")).encode()
            + b"\n\n"
        )

        result = self.rewriter.rewrite_streaming_chunk(
            chunk, self.route_config_enabled
        )

        # Should return original chunk unchanged
        assert result == chunk

    def test_rewrite_streaming_chunk_disabled_route(self):
        """Test that disabled route returns chunk unchanged."""
        chunk_data = {
            "id": "test-123",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "<read><path>README.md</path></read>",
                    },
                    "finish_reason": None,
                }
            ],
        }
        chunk = (
            b"data: "
            + json.dumps(chunk_data, separators=(",", ":")).encode()
            + b"\n\n"
        )

        result = self.rewriter.rewrite_streaming_chunk(
            chunk, self.route_config_disabled
        )

        # Should return original chunk unchanged
        assert result == chunk

    def test_rewrite_streaming_chunk_not_a_chunk(self):
        """Test that non-chunk bytes are returned unchanged."""
        chunk = b"not a valid SSE chunk"
        result = self.rewriter.rewrite_streaming_chunk(
            chunk, self.route_config_enabled
        )

        assert result == chunk

    def test_rewrite_response_body_nested_pattern(self):
        """Test rewriting non-streaming response body with nested pattern."""
        body = {
            "id": "test-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "<read><path>README.md</path></read>",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

        result = self.rewriter.rewrite_response_body(body, self.route_config_enabled)

        assert result is not None
        assert "choices" in result
        assert len(result["choices"]) > 0

        message = result["choices"][0]["message"]
        assert "tool_calls" in message
        assert "content" not in message
        assert message["tool_calls"][0]["function"]["name"] == "read"

    def test_rewrite_response_body_no_rewrite_needed(self):
        """Test that response without pseudo-tool-call is returned unchanged."""
        body = {
            "id": "test-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "This is regular text",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

        result = self.rewriter.rewrite_response_body(body, self.route_config_enabled)

        # Should return original body unchanged
        assert result == body

    def test_rewrite_response_body_disabled_route(self):
        """Test that disabled route returns body unchanged."""
        body = {
            "id": "test-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "<read><path>README.md</path></read>",
                    },
                    "finish_reason": "stop",
                }
            ],
        }

        result = self.rewriter.rewrite_response_body(body, self.route_config_disabled)

        # Should return original body unchanged
        assert result == body

    def test_rewrite_streaming_chunk_tool_call_only(self):
        """Test rewriting chunk that is tool-call only (no content)."""
        chunk_data = {
            "id": "test-123",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "name": "read",
                                    "arguments": '{"path":"README.md"}',
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
        chunk = (
            b"data: "
            + json.dumps(chunk_data, separators=(",", ":")).encode()
            + b"\n\n"
        )

        result = self.rewriter.rewrite_streaming_chunk(
            chunk, self.route_config_enabled
        )

        # Should return unchanged (already a proper tool call)
        assert result == chunk

    def test_rewrite_streaming_chunk_drops_chunk_when_parsing_fails(self):
        """Test that chunk is not dropped when parsing fails."""
        chunk_data = {
            "id": "test-123",
            "object": "chat.completion.chunk",
            "created": 1234567890,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "<read><path>README.md</path></read>",
                    },
                    "finish_reason": None,
                }
            ],
        }
        chunk = (
            b"data: "
            + json.dumps(chunk_data, separators=(",", ":")).encode()
            + b"\n\n"
        )

        # Create rewriter with unsupported pattern
        disabled_parser_rewriter = ToolCallRewriter(
            enabled=True, supported_patterns=["separate"]  # Only separate, not nested
        )

        result = disabled_parser_rewriter.rewrite_streaming_chunk(
            chunk, self.route_config_enabled
        )

        # Should return original chunk (not drop it)
        assert result == chunk


class TestPseudoToolCallParserEdgeCases:
    """Edge case tests for PseudoToolCallParser."""

    def setup_method(self):
        """Set up test fixtures."""
        self.parser = PseudoToolCallParser()

    def test_parse_with_html_tags_in_content(self):
        """Test parsing when content contains HTML-like tags.

        Nested <b> inside <path> means path has no direct text → no args extracted.
        Now returns tool call with zero args (valid, previously returned None).
        """
        text = "<read><path><b>README.md</b></path></read>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        assert result.arguments == {}

    def test_parse_with_unicode_content(self):
        """Test parsing when content contains unicode characters."""
        text = "<read><path>README_日本語.md</path></read>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        assert result.arguments == {"path": "README_日本語.md"}

    def test_parse_with_numeric_values(self):
        """Test parsing when arguments contain numeric values."""
        text = "<query><page>1</page><limit>10</limit></query>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "query"
        # Values are strings, not numbers
        assert result.arguments == {"page": "1", "limit": "10"}

    def test_parse_with_empty_value(self):
        """Test parsing when an argument has empty value."""
        text = "<read><path>README.md</path><mode></mode></read>"
        result = self.parser.parse(text)

        assert result is not None
        assert result.name == "read"
        # Empty values are stripped by the parser, so only non-empty args are returned
        assert result.arguments == {"path": "README.md"}

    def test_parse_only_whitespace_name(self):
        """Test parsing when name tag contains only whitespace."""
        text = "<name>   </name><path>README.md</path>"
        result = self.parser.parse(text)

        # Should return None because name is empty after stripping
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])