"""
MultimodalValidatorFilter — validates and repairs multimodal (vision) payloads.

Detects inconsistencies between image markers in text and actual image_url
parts in messages, preventing the upstream tokenizer error:

    tokenize: error: number of bitmaps (N) does not match number of markers (M)

Runs at priority 30 (after summarization at 15, before nudge at 50).
Request-phase only — no streaming chunk processing needed.
"""

import re
from typing import Any, Dict, List, Optional, Pattern

from keeprollming.logger import log
from keeprollming.orchestrator.filter import (
    Filter,
    FilterConfig,
    FilterExecutionContext,
    Request,
    Response,
)
from keeprollming.orchestrator.filters.events import emit_filter_event

_FILE_PART_TYPES = {"file", "input_file", "document", "file_url", "document_url"}

# Default marker patterns — cover llama.cpp (mtmd), Qwen, LLaVA, and common formats
# llama.cpp's mtmd_tokenizer uses "<__media__>" as the media marker (hardcoded default).
# Other patterns cover common image/vision tokens from various model tokenizers.
_DEFAULT_MARKER_PATTERNS: List[str] = [
    r"<__media__>",  # llama.cpp mtmd default media marker
    r"<image>",
    r"<img>",
    r"<\|image_pad\|>",
    r"<\|vision_start\|>.*?<\|vision_end\|>",
    r"!\[image\]\(.*?\)",
]

# The llama.cpp <__media__> marker is special: it should ALWAYS be stripped from text
# because llama.cpp's server auto-inserts the correct number of <__media__> markers
# based on image_url items in the content list. Its presence in text is always an
# artifact (conversation history, previous responses, etc.).
_LLAMACPP_MEDIA_MARKER = r"<__media__>"


class MultimodalValidatorFilter(Filter):
    """Validate and repair multimodal content in request messages.

    Priority 30 — after summarization (15), before nudge (50).

    Configuration (from the route ``filters`` mapping):
        enabled: bool (default: true)
        strip_orphaned_markers: bool (default: true)
            When true, remove image markers from text when no matching
            image_url parts exist. When false, only log warnings.
        marker_patterns: list[str] (default: Qwen + common patterns)
            Regex patterns used to detect image markers in text.
        log_level: str (default: "WARN")
            Log level for detected mismatches.
        max_images: int (default: 0)
            Maximum number of image_url items allowed in a single request.
            When exceeded, excess images are removed and replaced with a
            text placeholder. 0 = no limit.
        max_images_policy: "strip_first" | "strip_latest" (default: "strip_first")
            Selects which excess images are removed. ``strip_first`` removes
            the oldest images and retains the most recent ones; ``strip_latest``
            removes the newest images and retains the oldest ones.
        max_images_replacement_text: str (default: "[Image omitted due to limit]")
            Text inserted in place of stripped image_url items when
            max_images is exceeded.
        strip_all_images: bool (default: false)
            When true, ALL image_url items are replaced with the replacement
            text. Useful as a kill-switch when upstream can't handle any
            images in tool messages. Overrides max_images.
        strip_all_files: bool (default: false)
            Replace structured file/document parts across the entire history,
            including PDFs incorrectly carried as image parts. Real images
            and plain text are preserved.
        files_replacement_text: str (default: "[File omitted: attachments are disabled]")
            Placeholder replacing each removed file, preserving message shape.
    """

    _default_name = "multimodal_validator"
    priority = 30
    supports_streaming = True
    supports_non_streaming = True

    def __init__(self, config=None):
        """Initialize MultimodalValidatorFilter from config dict or FilterConfig."""
        if isinstance(config, dict):
            enabled = config.get("enabled", True)
            self._strip = config.get("strip_orphaned_markers", True)
            patterns_raw = config.get("marker_patterns", _DEFAULT_MARKER_PATTERNS)
            self._log_level = config.get("log_level", "WARN").upper()
            self._max_images = config.get("max_images", 0)
            self._max_images_policy = config.get("max_images_policy", "strip_first")
            if self._max_images_policy not in {"strip_first", "strip_latest"}:
                raise ValueError("multimodal_validator.max_images_policy must be 'strip_first' or 'strip_latest'")
            self._max_images_replacement = config.get(
                "max_images_replacement_text",
                "[Image omitted due to limit]",
            )
            self._strip_all = config.get("strip_all_images", False)
            self._strip_all_files = config.get("strip_all_files", False)
            self._files_replacement = config.get("files_replacement_text", "[File omitted: attachments are disabled]")
            base_config = FilterConfig(enabled=enabled, name="multimodal_validator")
            super().__init__(base_config)
        else:
            super().__init__(config)
            self._strip = True
            patterns_raw = _DEFAULT_MARKER_PATTERNS
            self._log_level = "WARN"
            self._max_images = 0
            self._max_images_policy = "strip_first"
            self._max_images_replacement = "[Image omitted due to limit]"
            self._strip_all = False
            self._strip_all_files = False
            self._files_replacement = "[File omitted: attachments are disabled]"

        self._patterns: List[Pattern] = [re.compile(p) for p in patterns_raw]
        # Always compile <__media__> pattern separately for unconditional stripping
        self._media_pattern: Pattern = re.compile(_LLAMACPP_MEDIA_MARKER)

    # ── Public API ───────────────────────────────────────────────────

    async def process_request(self, request: Request, context: FilterExecutionContext) -> Request:
        """Validate and repair multimodal content in request messages."""
        if not self.is_enabled:
            return request

        req_id = self._resolve_req_id(context)
        modified = False

        if self._strip_all_files:
            stripped = self._remove_files(request.messages)
            if stripped:
                modified = True
                emit_filter_event(
                    context,
                    "multimodal_validator",
                    "filter.multimodal_validator.files_stripped",
                    level="BASIC",
                    stripped=stripped,
                )

        for idx, msg in enumerate(request.messages):
            result = self._validate_message(msg)
            if result is not None:
                request.messages[idx] = result
                modified = True

        # ── Max images enforcement ───────────────────────────────────
        if self._max_images > 0:
            total_images = self._count_total_images(request.messages)
            if total_images > self._max_images:
                stripped = self._enforce_max_images(request.messages)
                if stripped > 0:
                    modified = True
                    log(
                        "WARN",
                        "multimodal_validator_max_images_exceeded",
                        req_id=req_id,
                        total_images=total_images,
                        max_images=self._max_images,
                        max_images_policy=self._max_images_policy,
                        stripped=stripped,
                    )

        # ── Strip all images kill-switch ─────────────────────────────
        if self._strip_all:
            total_images = self._count_total_images(request.messages)
            if total_images > 0:
                # Temporarily set max_images to 0 to strip everything
                saved_max = self._max_images
                self._max_images = 0
                stripped = self._enforce_max_images(request.messages)
                self._max_images = saved_max
                if stripped > 0:
                    modified = True
                    log(
                        "WARN",
                        "multimodal_validator_strip_all_images",
                        req_id=req_id,
                        stripped=stripped,
                    )

        if modified:
            log(
                "INFO",
                "multimodal_validator_applied",
                req_id=req_id,
                message_count=len(request.messages),
                strip_enabled=self._strip,
            )

        return request

    async def process_response(self, response: Response, context: FilterExecutionContext) -> Response:
        """Pass through — this filter only modifies requests."""
        return response

    def reset(self) -> None:
        pass

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _is_file_part(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        kind = item.get("type")
        if not isinstance(kind, str):
            return False
        if kind in _FILE_PART_TYPES:
            return True
        if kind in {"image", "image_url", "input_image"}:
            source = item.get("source")
            if isinstance(source, dict) and str(source.get("media_type", "")).lower() == "application/pdf":
                return True
            url = item.get("image_url", "")
            if isinstance(url, dict):
                url = url.get("url", "")
            return isinstance(url, str) and re.match(r"data:application/pdf[;,]", url, re.IGNORECASE) is not None
        return False

    def _remove_files(self, messages: List[Dict[str, Any]]) -> int:
        """Replace file parts without dropping messages, tool IDs or tool calls."""
        stripped = 0
        for index, message in enumerate(messages):
            content = message.get("content")
            if not isinstance(content, list):
                continue
            new_content = []
            changed = False
            for item in content:
                if self._is_file_part(item):
                    new_content.append({"type": "text", "text": self._files_replacement})
                    stripped += 1
                    changed = True
                else:
                    new_content.append(item)
            if changed:
                messages[index] = dict(message, content=new_content)
        return stripped

    def _resolve_req_id(self, context: FilterExecutionContext) -> str:
        """Extract request ID from context."""
        return context.req_id or "unknown"

    def _count_total_images(self, messages: List[Dict[str, Any]]) -> int:
        """Count ALL image_url items across all messages."""
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                total += self._count_image_parts(content)
        return total

    def _enforce_max_images(self, messages: List[Dict[str, Any]]) -> int:
        """Enforce max_images limit by removing excess image_url items.

        ``strip_first`` (the default) removes the oldest image parts and keeps
        the newest ``max_images``. ``strip_latest`` retains the historical
        behavior: keep the oldest ``max_images`` and remove newer parts. Each
        stripped image_url item is replaced with a text placeholder so the
        message structure is preserved.

        Returns the number of image_url items stripped.
        """
        image_positions: List[tuple[int, int]] = []
        for message_index, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for item_index, item in enumerate(content):
                if isinstance(item, dict) and item.get("type") == "image_url":
                    image_positions.append((message_index, item_index))

        excess = len(image_positions) - self._max_images
        if excess <= 0:
            return 0

        if self._max_images_policy == "strip_first":
            positions_to_strip = set(image_positions[:excess])
        else:
            positions_to_strip = set(image_positions[self._max_images :])

        for message_index, msg in enumerate(messages):
            content = msg.get("content")
            if not isinstance(content, list):
                continue

            new_content: List[Dict[str, Any]] = []
            for item_index, item in enumerate(content):
                if not isinstance(item, dict):
                    new_content.append(item)
                    continue
                if (message_index, item_index) in positions_to_strip:
                    new_content.append(
                        {
                            "type": "text",
                            "text": self._max_images_replacement,
                        }
                    )
                else:
                    new_content.append(item)

            msg["content"] = new_content

        return excess

    def _count_image_parts(self, content: List[Dict[str, Any]]) -> int:
        """Count image_url items in a list-type content field."""
        return sum(1 for item in content if isinstance(item, dict) and item.get("type") == "image_url")

    def _count_markers_in_text(self, text: str) -> int:
        """Count image markers found in a text string using configured patterns."""
        total = 0
        for pattern in self._patterns:
            matches = pattern.findall(text)
            total += len(matches)
        return total

    def _strip_markers_from_text(self, text: str) -> str:
        """Remove all image markers from text using configured patterns."""
        result = text
        for pattern in self._patterns:
            result = pattern.sub("", result)
        return result.strip()

    def _validate_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check a single message for multimodal inconsistencies.

        Returns the repaired message dict if changes were made,
        or None if the message was already consistent.
        """
        content = msg.get("content")

        if isinstance(content, list):
            return self._validate_multimodal_list(msg, content)
        elif isinstance(content, str):
            return self._validate_text_content(msg, content)

        return None

    def _validate_multimodal_list(self, msg: Dict[str, Any], content: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Validate a message with content as a list (structured multimodal).

        Counts image_url parts and checks text parts for orphaned markers.

        Special handling:
        - <__media__> (llama.cpp mtmd marker) is ALWAYS stripped from text
          parts unconditionally, because llama.cpp's server auto-inserts the
          correct count based on image_url items. Its presence in text is
          always an artifact (conversation history, previous model responses).
        - Other markers are stripped only when count exceeds image_url count.
        """
        # Phase 1: Always strip <__media__> from all text parts unconditionally
        media_stripped = self._strip_media_from_list_content(content)
        if media_stripped is not None:
            content = media_stripped

        image_count = self._count_image_parts(content)
        if image_count == 0:
            # No images in list — check text parts for orphaned markers
            result = self._validate_multimodal_list_text_parts(msg, content)
            # If media was stripped and we already modified content, wrap it
            if result is None and media_stripped is not None:
                result = dict(msg)
                result["content"] = content
            return result

        # Phase 2: Check other markers (non-<__media__>) for count-based stripping
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        ]
        combined_text = " ".join(text_parts)
        marker_count = self._count_markers_in_text(combined_text)

        if marker_count > image_count:
            # More markers than images — strip orphaned non-<__media__> markers
            new_content = self._strip_markers_from_list_content(content, image_count)
            if new_content is not None:
                result = dict(msg)
                result["content"] = new_content
                self._log_mismatch(
                    "list_content_orphaned_markers",
                    markers=marker_count,
                    images=image_count,
                    action="stripped" if self._strip else "detected",
                )
                return result
        elif marker_count < image_count and marker_count > 0:
            log(
                "DEBUG",
                "multimodal_validator_extra_images",
                images=image_count,
                markers=marker_count,
            )

        # If <__media__> was stripped but no other markers need fixing, return modified content
        if media_stripped is not None:
            result = dict(msg)
            result["content"] = content
            return result

        return None

    def _validate_multimodal_list_text_parts(
        self, msg: Dict[str, Any], content: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Validate a multimodal list that has text parts but no image_url parts.

        Strips orphaned markers from text parts if found.
        """
        text_only_markers = 0
        new_content: List[Dict[str, Any]] = []

        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if isinstance(text, str):
                    markers = self._count_markers_in_text(text)
                    text_only_markers += markers
                    if markers > 0 and self._strip:
                        cleaned = self._strip_markers_from_text(text)
                        if cleaned:
                            new_content.append({"type": "text", "text": cleaned})
                        # If nothing left after stripping, skip the text item
                    else:
                        new_content.append(item)
                else:
                    new_content.append(item)
            else:
                new_content.append(item)

        if text_only_markers > 0:
            if self._strip:
                # Rebuild content list without orphaned markers and empty text items
                result = dict(msg)
                result["content"] = [item for item in new_content if self._has_content(item)]
                action = "stripped"
            else:
                action = "detected"
                result = None

            self._log_mismatch(
                "text_only_orphaned_markers",
                markers=text_only_markers,
                images=0,
                action=action,
            )
            return result if self._strip else None

        return None

    def _strip_media_from_list_content(self, content: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """Unconditionally strip <__media__> markers from ALL text parts in a list content.

        llama.cpp's mtmd_tokenizer auto-inserts <__media__> markers based on
        image_url count, so any <__media__> already in the text is always an
        artifact and must be removed.

        Returns new content list, or None if no changes were needed.
        """
        new_content: List[Dict[str, Any]] = []
        changed = False

        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if isinstance(text, str):
                    cleaned = self._media_pattern.sub("", text)
                    if cleaned != text:
                        changed = True
                    if cleaned.strip():
                        new_content.append({"type": "text", "text": cleaned.strip()})
                else:
                    new_content.append(item)
            else:
                new_content.append(item)

        if not changed:
            return None

        return [item for item in new_content if self._has_content(item)]

    def _strip_markers_from_list_content(
        self, content: List[Dict[str, Any]], image_count: int
    ) -> Optional[List[Dict[str, Any]]]:
        """Strip orphaned markers from text parts in a list content, preserving image_count integrity.

        Returns new content list, or None if no changes needed.
        """
        if not self._strip:
            return None

        new_content: List[Dict[str, Any]] = []
        changed = False

        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if isinstance(text, str):
                    cleaned = self._strip_markers_from_text(text)
                    if cleaned != text:
                        changed = True
                    if cleaned:
                        new_content.append({"type": "text", "text": cleaned})
                else:
                    new_content.append(item)
            else:
                new_content.append(item)

        if not changed:
            return None

        return [item for item in new_content if self._has_content(item)]

    def _validate_text_content(self, msg: Dict[str, Any], content: str) -> Optional[Dict[str, Any]]:
        """Validate a message with string content for orphaned image markers.

        Returns repaired message or None if no changes needed.
        """
        marker_count = self._count_markers_in_text(content)
        if marker_count == 0:
            return None

        result = dict(msg)
        if self._strip:
            result["content"] = self._strip_markers_from_text(content)

        self._log_mismatch(
            "text_content_orphaned_markers",
            markers=marker_count,
            images=0,
            action="stripped" if self._strip else "detected",
        )
        return result if self._strip else None

    def _has_content(self, item: Any) -> bool:
        """Check if a content item has meaningful content."""
        if isinstance(item, dict):
            kind = item.get("type")
            if isinstance(kind, str) and (kind == "image_url" or kind in _FILE_PART_TYPES):
                return True
            text = item.get("text", "")
            return bool(text and text.strip())
        return False

    def _log_mismatch(
        self,
        event: str,
        *,
        markers: int,
        images: int,
        action: str,
    ) -> None:
        """Log a multimodal mismatch at the configured level."""
        log(
            self._log_level,
            event,
            markers=markers,
            images=images,
            action=action,
            strip_enabled=self._strip,
        )
