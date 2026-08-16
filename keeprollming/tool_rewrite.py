"""
Tool-Call Rewriting Module

This module provides middleware-like functionality to intercept pseudo-tool-call
XML/textual output from local models and convert them into OpenAI-compatible
structured tool calls.

Supported XML Patterns:
1. Nested: <tool_name><arg1>val1</arg1><arg2>val2</arg2></tool_name>
2. Separate: <name>tool_name</name><arg1>val1</arg1><arg2>val2</arg2>
3. Function-param: <function=name><parameter=val</parameter>...

Examples:
    Input:  <read><path>README.md</path></read>
    Output: {"type": "function", "function": {"name": "read", "arguments": "{\"path\": \"README.md\"}"}}

    Input:  <function=ReactJudgeOutput><parameter=has_issue>False</parameter></function>
    Output: {"type": "function", "function": {"name": "ReactJudgeOutput", "arguments": "{\"has_issue\": \"False\"}"}}
"""

from __future__ import annotations

import json
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .observability import events_tool_rewrite as _events


@dataclass
class PseudoToolCall:
    """Internal representation of a parsed pseudo-tool-call."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


class PseudoToolCallParser:
    """
    Parser for pseudo-tool-call XML formats.

    Supports three XML patterns:
    1. Nested: <tool_name><arg1>val1</arg1>...</tool_name>
    2. Separate: <name>tool_name</name><arg1>val1</arg1>...
    3. Function-param: <function=name><parameter=val</parameter>...
    """

    # Pattern to extract all XML tags and their content
    TAG_PATTERN = re.compile(r"<([a-zA-Z_][a-zA-Z0-9_-]*)>([^<]*)</\1>")

    # Pattern 3 regex for <function=name><parameter=val</parameter> format
    FUNCTION_PATTERN = re.compile(
        r"<function=([a-zA-Z_][a-zA-Z0-9_-]*)>"  # Extract function name from <function=name>
        r"[\s\S]*?"
        r"</function>",
        re.IGNORECASE,
    )

    # Pattern for extracting <parameter=name>value</parameter> content
    PARAMETER_PATTERN = re.compile(
        r"<parameter=([a-zA-Z_][a-zA-Z0-9_-]*)>([^<]*)</parameter>",
        re.IGNORECASE,
    )

    def __init__(self, supported_patterns: Optional[List[str]] = None):
        """
        Initialize the parser.

        Args:
            supported_patterns: List of patterns to support ("nested", "separate", "function").
                              Defaults to all if None.
        """
        self.supported_patterns = supported_patterns or ["nested", "separate", "function"]

    def parse(self, text: str) -> Optional[PseudoToolCall]:
        """
        Parse a pseudo-tool-call XML from text.

        Args:
            text: The text to parse (may contain XML or plain text)

        Returns:
            PseudoToolCall if valid XML pattern detected, None otherwise
        """
        if not text or not isinstance(text, str):
            return None

        text = text.strip()

        # Check if text contains XML-like structure
        if not self._is_xml_like(text):
            return None

        try:
            # Try Pattern 2 first: <name>tool</name><arg>val</arg>...
            if "separate" in self.supported_patterns:
                result = self._parse_separate_pattern(text)
                if result:
                    return result

            # Try Pattern 1: <tool_name><arg>val</arg>...</tool_name>
            if "nested" in self.supported_patterns:
                result = self._parse_nested_pattern(text)
                if result:
                    return result

            # Try Pattern 3: <function=name><parameter=val</parameter>...
            if "function" in self.supported_patterns:
                result = self._parse_function_pattern(text)
                if result:
                    return result

        except ET.ParseError:
            # Invalid XML, fall through
            pass
        except Exception as e:
            # Log the error for debugging; return None to avoid breaking the pipeline
            import traceback
            _events.emit_parse_error(error=str(e), traceback=traceback.format_exc())
            pass

        return None

    def _is_xml_like(self, text: str) -> bool:
        """Check if text looks like XML with tool-call structure."""
        # Must have at least one XML tag (supports <tag>, <attr=val>, and <tag attr="val">)
        if not re.search(r"<[a-zA-Z_][a-zA-Z0-9_-]*(?:\s|=|>)", text):
            return False

        # Must have either <name> tag (pattern 2) or nested structure (pattern 1)
        if "<name>" in text:
            return True

        # Check for nested pattern: <tool_name><arg>...</arg>...</tool_name>
        if self.TAG_PATTERN.search(text):
            return True

        # Check for function pattern: <function=name><parameter>...</parameter>...
        if "<function=" in text and "</function>" in text:
            return True

        return False

    def _parse_separate_pattern(self, text: str) -> Optional[PseudoToolCall]:
        """
        Parse Pattern 2: <name>tool_name</name><arg1>val1</arg1>...
        """
        # Extract tool name
        name_match = re.search(r"<name>\s*([^<]+)\s*</name>", text, re.IGNORECASE)
        if not name_match:
            return None

        tool_name = name_match.group(1).strip()
        if not tool_name:
            return None

        # Extract all argument tags
        arguments: Dict[str, Any] = {}
        for match in self.TAG_PATTERN.finditer(text):
            tag_name = match.group(1)
            tag_content = match.group(2).strip()

            # Skip the <name> tag itself
            if tag_name.lower() == "name":
                continue

            # Store argument (simple string values)
            arguments[tag_name] = tag_content

        return PseudoToolCall(name=tool_name, arguments=arguments)

    def _parse_nested_pattern(self, text: str) -> Optional[PseudoToolCall]:
        """
        Parse Pattern 1: <tool_name><arg1>val1</arg1>...</tool_name>
        """
        # Try to parse as XML
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None

        tool_name = root.tag

        # Extract all child elements as arguments
        arguments: Dict[str, Any] = {}
        for child in root:
            if child.text:
                arguments[child.tag] = child.text.strip()

        return PseudoToolCall(name=tool_name, arguments=arguments)

    def _parse_function_pattern(self, text: str) -> Optional[PseudoToolCall]:
        """
        Parse Pattern 3: <function=name><parameter=val</parameter>...
        """
        # Extract function name from <function=name> tag
        func_match = self.FUNCTION_PATTERN.search(text)
        if not func_match:
            return None

        tool_name = func_match.group(1)
        if not tool_name:
            return None

        # Extract all parameter tags
        arguments: Dict[str, Any] = {}
        for match in self.PARAMETER_PATTERN.finditer(text):
            param_name = match.group(1)
            param_value = match.group(2).strip()
            arguments[param_name] = param_value

        return PseudoToolCall(name=tool_name, arguments=arguments)


class ToolCallRewriter:
    """
    Middleware for rewriting pseudo-tool-calls to OpenAI-compatible format.

    This class handles the conversion of parsed pseudo-tool-calls into
    structured tool calls that match the OpenAI API format.
    """

    def __init__(
        self,
        enabled: bool = False,
        supported_patterns: Optional[List[str]] = None,
        request_id: Optional[str] = None,
    ):
        """
        Initialize the rewriter.

        Args:
            enabled: Whether rewriting is enabled
            supported_patterns: Patterns to support ("nested", "separate")
            request_id: Request ID for generating unique call IDs
        """
        self.enabled = enabled
        self.parser = PseudoToolCallParser(
            supported_patterns=supported_patterns or ["nested", "separate"]
        )
        self.request_id = request_id or str(uuid.uuid4())[:8]

    def should_rewrite(self, route_config: Dict[str, Any]) -> bool:
        """
        Determine if rewriting should be applied for a given route config.

        Args:
            route_config: Route configuration dictionary

        Returns:
            True if rewriting should be applied, False otherwise
        """
        if not self.enabled:
            return False

        # Check route-specific override
        return route_config.get("tool_rewrite_enabled", False)

    def rewrite_streaming_chunk(
        self,
        chunk: bytes,
        route_config: Dict[str, Any],
    ) -> Optional[bytes]:
        """
        Transform a streaming SSE chunk if it contains a pseudo-tool-call.

        Args:
            chunk: The SSE chunk to transform (bytes)
            route_config: Route configuration

        Returns:
            Transformed chunk bytes, or None if chunk should be dropped
        """
        if not self.should_rewrite(route_config):
            return chunk

        try:
            # Decode chunk
            chunk_str = chunk.decode("utf-8")

            # Check if this is an SSE data chunk
            if not chunk_str.startswith("data: "):
                return chunk

            # Extract payload
            payload_str = chunk_str[6:].strip()  # Remove "data: " prefix

            # Skip DONE marker
            if payload_str == "[DONE]":
                return chunk

            # Parse JSON
            try:
                obj = json.loads(payload_str)
            except json.JSONDecodeError:
                return chunk

            # Check if this is a chat.completion.chunk with content
            if not self._is_tool_call_chunk(obj):
                return chunk

            # Try to find and rewrite pseudo-tool-call in content
            new_obj = self._rewrite_content_in_chunk(obj)

            if new_obj is None:
                # No pseudo-tool-call found, return original
                return chunk

            # Re-encode as SSE
            return (
                "data: " + json.dumps(new_obj, separators=(",", ":")) + "\n\n"
           ).encode("utf-8")

        except Exception as e:
            # Log the error for debugging; return original chunk to avoid breaking the stream
            import traceback
            _events.emit_streaming_error(error=str(e), traceback=traceback.format_exc())
            return chunk

    def _is_tool_call_chunk(self, obj: Dict) -> bool:
        """Check if chunk has tool_calls structure or content that might need rewriting."""
        if not isinstance(obj, dict):
            return False

        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return False

        choice = choices[0]
        if not isinstance(choice, dict):
            return False

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            return False

        # Check if chunk already has tool_calls OR has content that might be a pseudo-tool-call
        has_tool_calls = "tool_calls" in delta
        has_content = isinstance(delta.get("content"), str) and bool(delta.get("content"))

        # We want to process chunks that have content (potential pseudo-tool-call)
        # or chunks that already have tool_calls (to skip)
        return has_content or has_tool_calls

    def _rewrite_content_in_chunk(
        self, obj: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Try to rewrite pseudo-tool-call content in a chunk.

        Returns:
            Modified chunk if rewriting successful, None if no pseudo-tool-call found
        """
        choices = obj.get("choices", [])
        if not choices or not isinstance(choices[0], dict):
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            return None

        # Get content if present
        content = delta.get("content")
        if not content or not isinstance(content, str):
            return None

        # Try to parse pseudo-tool-call from content
        pseudo_call = self.parser.parse(content)
        if not pseudo_call:
            return None

        # Convert to OpenAI tool call structure
        tool_call = self._convert_to_openai_tool_call(pseudo_call)

        # Build new chunk with tool call instead of content
        new_delta = delta.copy()
        new_delta["tool_calls"] = [tool_call]
        del new_delta["content"]  # Remove the content that was rewritten

        # Ensure role is set
        if "role" not in new_delta:
            new_delta["role"] = "assistant"

        new_choice = choice.copy()
        new_choice["delta"] = new_delta

        new_obj = obj.copy()
        new_obj["choices"] = [new_choice]

        return new_obj

    def _convert_to_openai_tool_call(
        self, pseudo_call: PseudoToolCall
    ) -> Dict[str, Any]:
        """
        Convert a PseudoToolCall to OpenAI-compatible tool call structure.

        Args:
            pseudo_call: The parsed pseudo-tool-call

        Returns:
            Dictionary matching OpenAI tool call format
        """
        # Generate unique call ID
        call_id = f"call_{self.request_id}_{uuid.uuid4().hex[:8]}"

        # Convert arguments to JSON string
        arguments_json = json.dumps(pseudo_call.arguments, separators=(",", ":"))

        return {
            "index": 0,
            "id": call_id,
            "type": "function",
            "function": {
                "name": pseudo_call.name,
                "arguments": arguments_json,
            },
        }

    def rewrite_response_body(
        self, body: Dict[str, Any], route_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Transform pseudo-tool-call in a non-streaming response body.

        Args:
            body: The response body to transform
            route_config: Route configuration

        Returns:
            Transformed response body, or original if no rewriting needed
        """
        if not self.should_rewrite(route_config):
            return body

        try:
            choices = body.get("choices", [])
            if not choices or not isinstance(choices[0], dict):
                return body

            choice = choices[0]
            message = choice.get("message", {})
            if not isinstance(message, dict):
                return body

            content = message.get("content")
            if not content or not isinstance(content, str):
                return body

            # Try to parse pseudo-tool-call from content
            pseudo_call = self.parser.parse(content)
            if not pseudo_call:
                return body

            # Convert to tool call
            tool_call = self._convert_to_openai_tool_call(pseudo_call)

            # Build new message with tool call
            new_message = message.copy()
            new_message["tool_calls"] = [tool_call]
            new_message["role"] = new_message.get("role") or "assistant"

            # Remove content
            if "content" in new_message:
                del new_message["content"]

            new_choice = choice.copy()
            new_choice["message"] = new_message

            new_body = body.copy()
            new_body["choices"] = [new_choice]

            return new_body

        except Exception as e:
            # Log the error for debugging; return original body to avoid breaking the response
            import traceback
            _events.emit_body_error(error=str(e), traceback=traceback.format_exc())
            return body