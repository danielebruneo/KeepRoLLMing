"""
Tests for MultimodalValidatorFilter — validates/repairs multimodal (vision) payloads.

Tests cover:
- List content with matching markers/images → no change
- List content with orphaned markers → markers stripped
- String content with image markers → markers stripped
- String content without markers → no change
- Empty/edge cases (empty list, empty string, no messages)
- Custom marker patterns from config
- strip_orphaned_markers: false mode (detect only)
- Pipeline integration
"""

import pytest

from keeprollming.orchestrator.filter import (
    FilterConfig,
    FilterExecutionContext,
    get_registered_filters,
)
from keeprollming.orchestrator.filters.multimodal_validator_filter import (
    MultimodalValidatorFilter,
)


# ── Mock Request ──────────────────────────────────────────────────────

class MockRequest:
    def __init__(self, messages=None, model="test-model", stream=False):
        self.messages = messages or []
        self.model = model
        self.stream = stream
        self.metadata = {}


# ── Context factory ──────────────────────────────────────────────────

def _make_context(req_id="test-req-001"):
    return FilterExecutionContext(req_id=req_id)


# ── Test data ────────────────────────────────────────────────────────

_TYPICAL_IMAGE_URL = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}
_TYPICAL_IMAGE_URL_2 = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ="}}


# ── Registration & basics ────────────────────────────────────────────

class TestRegistration:
    """Verify the filter is properly registered."""

    def test_filter_registered(self):
        registry = get_registered_filters()
        assert "multimodal_validator" in registry
        assert registry["multimodal_validator"] is MultimodalValidatorFilter

    def test_default_priority(self):
        assert MultimodalValidatorFilter.priority == 30

    def test_default_name(self):
        assert MultimodalValidatorFilter._default_name == "multimodal_validator"

    def test_supports_both_modes(self):
        f = MultimodalValidatorFilter()
        assert f.supports_streaming is True
        assert f.supports_non_streaming is True

    def test_default_config(self):
        f = MultimodalValidatorFilter()
        assert f.is_enabled is True
        assert f._strip is True
        assert f._log_level == "WARN"
        assert len(f._patterns) > 0


# ── String content tests ─────────────────────────────────────────────

class TestStringContent:
    """Test messages with plain string content."""

    @pytest.mark.asyncio
    async def test_no_markers_no_change(self):
        """String content without markers → no change."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": "Hello, describe this image"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"] == "Hello, describe this image"

    @pytest.mark.asyncio
    async def test_orphaned_image_marker_stripped(self):
        """String content with <image> marker but no images → marker stripped."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": "What is in <image> this image?"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "<image>" not in result.messages[0]["content"]
        assert "What is in  this image?" in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_orphaned_image_pad_stripped(self):
        """String content with <|image_pad|> marker stripped."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": "Look at <|image_pad|> this"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "<|image_pad|>" not in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_multiple_markers_stripped(self):
        """String with multiple <image> markers → all stripped."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": "<image> img1 <image> img2 <image> img3"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "<image>" not in result.messages[0]["content"]
        assert "img1" in result.messages[0]["content"]
        assert "img2" in result.messages[0]["content"]
        assert "img3" in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_vision_start_end_stripped(self):
        """Vision start/end markers stripped from string content."""
        f = MultimodalValidatorFilter()
        content = "Describe <|vision_start|><|image_pad|><|vision_end|> this"
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "<|vision_start|>" not in result.messages[0]["content"]
        assert "<|image_pad|>" not in result.messages[0]["content"]
        assert "<|vision_end|>" not in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_llamacpp_media_marker_stripped(self):
        """llama.cpp <__media__> marker stripped from string content."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": "Check <__media__> this screenshot"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "<__media__>" not in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_multiple_llamacpp_markers_stripped(self):
        """Multiple <__media__> markers in string → all stripped."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": "<__media__> pic1 <__media__> pic2 <__media__> pic3"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "<__media__>" not in result.messages[0]["content"]
        assert "pic1" in result.messages[0]["content"]
        assert "pic2" in result.messages[0]["content"]
        assert "pic3" in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_plain_text_unchanged(self):
        """Completely plain text without any markers → unchanged."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": "Just a regular text message."}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"] == "Just a regular text message."


# ── List content tests ───────────────────────────────────────────────

class TestListContent:
    """Test messages with list content (structured multimodal)."""

    @pytest.mark.asyncio
    async def test_matching_images_no_change(self):
        """List content with matching image parts and no markers → no change."""
        content = [
            {"type": "text", "text": "Describe these images"},
            dict(_TYPICAL_IMAGE_URL),
            dict(_TYPICAL_IMAGE_URL_2),
        ]
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        f = MultimodalValidatorFilter()
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"] == content

    @pytest.mark.asyncio
    async def test_list_with_orphaned_markers_stripped(self):
        """List content with more markers than images → markers stripped from text."""
        content = [
            {"type": "text", "text": "<image> <image> <image> Describe"},
            dict(_TYPICAL_IMAGE_URL),
        ]
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        f = MultimodalValidatorFilter()
        result = await f.process_request(req, _make_context())
        # 3 markers, 1 image → markers should be stripped
        assert "<image>" not in result.messages[0]["content"][0]["text"]
        # Image preserved
        assert len(result.messages[0]["content"]) == 2
        assert result.messages[0]["content"][1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_list_text_only_with_orphaned_markers(self):
        """List content with text parts only but containing markers → markers stripped."""
        content = [
            {"type": "text", "text": "Here is <image> the image"},
            {"type": "text", "text": "And another <image> here"},
        ]
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        f = MultimodalValidatorFilter()
        result = await f.process_request(req, _make_context())
        for item in result.messages[0]["content"]:
            assert "<image>" not in item.get("text", "")

    @pytest.mark.asyncio
    async def test_list_llamacpp_media_marker_stripped(self):
        """List with <__media__> markers but same number of images → markers stripped from text."""
        content = [
            {"type": "text", "text": "<__media__> <__media__> Describe these"},
            dict(_TYPICAL_IMAGE_URL),
            dict(_TYPICAL_IMAGE_URL_2),
        ]
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        f = MultimodalValidatorFilter()
        result = await f.process_request(req, _make_context())
        # 2 markers, 2 images → both markers should be stripped from text
        assert "<__media__>" not in result.messages[0]["content"][0]["text"]
        # Both images preserved
        assert len(result.messages[0]["content"]) == 3

    @pytest.mark.asyncio
    async def test_list_llamacpp_mixed_markers_stripped(self):
        """List with both <__media__> and <image> markers → <__media__> stripped unconditionally,
        <image> kept when count matches (1 marker → 1 image)."""
        content = [
            {"type": "text", "text": "<__media__> <image> Describe this"},
            dict(_TYPICAL_IMAGE_URL),
        ]
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        f = MultimodalValidatorFilter()
        result = await f.process_request(req, _make_context())
        # <__media__> always stripped unconditionally
        assert "<__media__>" not in result.messages[0]["content"][0]["text"]
        # <image> kept because 1 marker == 1 image (not orphaned)
        assert "<image>" in result.messages[0]["content"][0]["text"]
        # Images preserved
        assert len(result.messages[0]["content"]) == 2
        assert result.messages[0]["content"][1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_list_no_markers_unchanged(self):
        """List content without markers → unchanged."""
        content = [
            {"type": "text", "text": "Describe this scene"},
            dict(_TYPICAL_IMAGE_URL),
        ]
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        f = MultimodalValidatorFilter()
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"] == content

    @pytest.mark.asyncio
    async def test_list_mixed_content_types(self):
        """List with text and image_url items, markers match images → unchanged."""
        content = [
            {"type": "text", "text": "Compare these two images"},
            dict(_TYPICAL_IMAGE_URL),
            dict(_TYPICAL_IMAGE_URL_2),
        ]
        msgs = [{"role": "user", "content": content}]
        req = MockRequest(messages=msgs)
        f = MultimodalValidatorFilter()
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"] == content


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and unusual inputs."""

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """No messages → no error."""
        f = MultimodalValidatorFilter()
        req = MockRequest(messages=[])
        result = await f.process_request(req, _make_context())
        assert result.messages == []

    @pytest.mark.asyncio
    async def test_empty_content_string(self):
        """Empty string content → no change."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": ""}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"] == ""

    @pytest.mark.asyncio
    async def test_empty_content_list(self):
        """Empty list content → no change."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "user", "content": []}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"] == []

    @pytest.mark.asyncio
    async def test_missing_content_key(self):
        """Message without content key → no error."""
        f = MultimodalValidatorFilter()
        msgs = [{"role": "system"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "content" not in result.messages[0]

    @pytest.mark.asyncio
    async def test_multiple_messages_some_affected(self):
        """Multiple messages, only one with orphaned markers."""
        f = MultimodalValidatorFilter()
        msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Normal text"},
            {"role": "user", "content": "Text with <image> marker"},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        # First two unchanged
        assert result.messages[0]["content"] == "You are a helpful assistant."
        assert result.messages[1]["content"] == "Normal text"
        # Third has marker stripped
        assert "<image>" not in result.messages[2]["content"]

    @pytest.mark.asyncio
    async def test_disabled_filter_no_change(self):
        """Filter disabled → no modification."""
        f = MultimodalValidatorFilter(config={"enabled": False})
        msgs = [{"role": "user", "content": "Text with <image> marker"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "<image>" in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_detect_only_mode(self):
        """strip_orphaned_markers: false → markers detected but not removed."""
        f = MultimodalValidatorFilter(config={"strip_orphaned_markers": False})
        msgs = [{"role": "user", "content": "What is in <image> this image?"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        # Content should remain unchanged
        assert result.messages[0]["content"] == "What is in <image> this image?"


# ── Custom config tests ──────────────────────────────────────────────

class TestCustomConfig:
    """Custom marker patterns and configuration."""

    @pytest.mark.asyncio
    async def test_custom_marker_pattern(self):
        """Custom marker pattern strips custom tags."""
        config = {
            "marker_patterns": [r"\[img\]"],
        }
        f = MultimodalValidatorFilter(config=config)
        msgs = [{"role": "user", "content": "See [img] this image"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert "[img]" not in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_custom_log_level(self):
        """Custom log level accepted."""
        config = {"log_level": "DEBUG"}
        f = MultimodalValidatorFilter(config=config)
        assert f._log_level == "DEBUG"

    @pytest.mark.asyncio
    async def test_empty_marker_patterns(self):
        """Empty marker patterns → no markers detected → no changes."""
        config = {"marker_patterns": []}
        f = MultimodalValidatorFilter(config=config)
        msgs = [{"role": "user", "content": "Text with <image> marker"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        # No patterns → nothing detected → nothing stripped
        assert "<image>" in result.messages[0]["content"]

    @pytest.mark.asyncio
    async def test_case_sensitive_patterns(self):
        """Patterns are case-sensitive by default."""
        config = {"marker_patterns": [r"<Image>"]}
        f = MultimodalValidatorFilter(config=config)
        msgs = [{"role": "user", "content": "Text with <image> and <Image> markers"}]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        # Only <Image> (capital I) should be stripped
        assert "<Image>" not in result.messages[0]["content"]
        assert "<image>" in result.messages[0]["content"]


# ── Pipeline integration tests ───────────────────────────────────────

class TestPipelineIntegration:
    """Integration via Pipeline.from_route_config()."""

    def test_pipeline_route_config_resolves(self):
        """Pipeline.from_route_config resolves multimodal_validator."""
        from keeprollming.orchestrator.pipeline import Pipeline

        config = {
            "order": ["multimodal_validator"],
            "filters": {
                "multimodal_validator": {
                    "enabled": True,
                    "strip_orphaned_markers": True,
                }
            },
        }
        pipeline = Pipeline.from_route_config(config)
        assert pipeline is not None
        assert len(pipeline.filters) == 1
        assert pipeline.filters[0]._default_name == "multimodal_validator"
        assert pipeline.filters[0].is_enabled is True

    @pytest.mark.asyncio
    async def test_pipeline_process_request_strips_markers(self):
        """Pipeline running multimodal_validator strips orphaned markers."""
        from keeprollming.orchestrator.pipeline import Pipeline

        config = {
            "order": ["multimodal_validator"],
            "filters": {
                "multimodal_validator": {
                    "enabled": True,
                }
            },
        }
        pipeline = Pipeline.from_route_config(config)
        assert pipeline is not None

        payload = {
            "messages": [
                {"role": "user", "content": "What is in <image> this image?"}
            ],
            "model": "test-model",
        }
        result = await pipeline.process_request(
            payload, req_id="test-001",
            upstream_model="test-model",
        )
        assert "<image>" not in result["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_pipeline_no_op_when_consistent(self):
        """Pipeline does not modify consistent multimodal payloads."""
        from keeprollming.orchestrator.pipeline import Pipeline

        config = {
            "order": ["multimodal_validator"],
            "filters": {
                "multimodal_validator": {
                    "enabled": True,
                }
            },
        }
        pipeline = Pipeline.from_route_config(config)
        assert pipeline is not None

        # No markers in content
        content = [
            {"type": "text", "text": "Describe this image"},
            dict(_TYPICAL_IMAGE_URL),
        ]
        payload = {
            "messages": [{"role": "user", "content": content}],
            "model": "test-model",
        }
        result = await pipeline.process_request(
            payload, req_id="test-002",
            upstream_model="test-model",
        )
        assert result["messages"][0]["content"] == content


# ── Max images enforcement tests ────────────────────────────────────

class TestMaxImagesEnforcement:
    """Max images limit enforcement."""

    @pytest.mark.asyncio
    async def test_no_limit_no_change(self):
        """No max_images configured → all images preserved."""
        f = MultimodalValidatorFilter()
        msgs = [
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert len(result.messages) == 3
        for m in result.messages:
            assert m["content"][0]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_within_limit_no_change(self):
        """Images within limit → all preserved."""
        f = MultimodalValidatorFilter(config={"max_images": 5})
        msgs = [
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert len(result.messages) == 2
        for m in result.messages:
            assert m["content"][0]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_exceeds_limit_strips_latest(self):
        """Images exceed limit → latest images stripped, replaced with text."""
        f = MultimodalValidatorFilter(config={"max_images": 2})
        msgs = [
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL, _image_note="img1")]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL, _image_note="img2")]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL, _image_note="img3")]},
        ]
        # Rename the keys for clarity in test
        msgs[0]["content"][0]["_note"] = "img1"
        msgs[1]["content"][0]["_note"] = "img2"
        msgs[2]["content"][0]["_note"] = "img3"
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        # First 2 images preserved (oldest), last one replaced
        assert result.messages[0]["content"][0]["type"] == "image_url"
        assert result.messages[1]["content"][0]["type"] == "image_url"
        # Most recent (msg[2]) replaced with text placeholder
        assert result.messages[2]["content"][0]["type"] == "text"
        assert "[Image omitted" in result.messages[2]["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_exceeds_limit_multiple_images_per_message(self):
        """Multiple images in one message → limit enforced per-item."""
        f = MultimodalValidatorFilter(config={"max_images": 2})
        msgs = [
            {"role": "tool", "content": [
                dict(_TYPICAL_IMAGE_URL),
                dict(_TYPICAL_IMAGE_URL),
                dict(_TYPICAL_IMAGE_URL),
            ]},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        content = result.messages[0]["content"]
        assert content[0]["type"] == "image_url"
        assert content[1]["type"] == "image_url"
        assert content[2]["type"] == "text"
        assert "[Image omitted" in content[2]["text"]

    @pytest.mark.asyncio
    async def test_custom_replacement_text(self):
        """Custom replacement text used instead of default."""
        f = MultimodalValidatorFilter(config={
            "max_images": 1,
            "max_images_replacement_text": "[skipped]",
        })
        msgs = [
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"][0]["type"] == "image_url"
        assert result.messages[1]["content"][0]["type"] == "text"
        assert result.messages[1]["content"][0]["text"] == "[skipped]"

    @pytest.mark.asyncio
    async def test_mixed_content_preserved(self):
        """Non-image content items preserved when image stripped."""
        f = MultimodalValidatorFilter(config={"max_images": 1})
        msgs = [
            {"role": "tool", "content": [
                {"type": "text", "text": "Result:"},
                dict(_TYPICAL_IMAGE_URL),
                {"type": "text", "text": "End"},
                dict(_TYPICAL_IMAGE_URL),
            ]},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        content = result.messages[0]["content"]
        # First image preserved, text items preserved, second image replaced
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "Result:"
        assert content[1]["type"] == "image_url"
        assert content[2]["type"] == "text"
        assert content[2]["text"] == "End"
        assert content[3]["type"] == "text"
        assert "[Image omitted" in content[3]["text"]

    @pytest.mark.asyncio
    async def test_strip_all_images(self):
        """strip_all_images: true → ALL images replaced with text."""
        f = MultimodalValidatorFilter(config={"strip_all_images": True})
        msgs = [
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        for m in result.messages:
            for p in m["content"]:
                assert p["type"] == "text"
                assert "[Image omitted" in p["text"]

    @pytest.mark.asyncio
    async def test_strip_all_off_by_default(self):
        """strip_all_images defaults to false → all images preserved."""
        f = MultimodalValidatorFilter()
        msgs = [
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
        ]
        req = MockRequest(messages=msgs)
        result = await f.process_request(req, _make_context())
        assert result.messages[0]["content"][0]["type"] == "image_url"


# ── Process_response passthrough ─────────────────────────────────────

class TestResponsePassthrough:
    """Response phase should pass through unchanged."""

    @pytest.mark.asyncio
    async def test_response_unchanged(self):
        """process_response returns the response unchanged."""
        class MockResp:
            content = "test response"
            model = "test-model"
            finish_reason = "stop"
            tool_calls = None
            usage = None

        f = MultimodalValidatorFilter()
        resp = MockResp()
        result = await f.process_response(resp, _make_context())
        assert result is resp
        assert result.content == "test response"


# ── strip_last_image_url tests ──────────────────────────────────────

class TestStripLastImageUrl:
    """Tests for _strip_last_image_url helper."""

    def test_strip_last_image_from_single_msg(self):
        from keeprollming.endpoints.streaming_handlers import _strip_last_image_url
        msgs = [{"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]}]
        result = _strip_last_image_url(msgs)
        assert result == 1
        assert msgs[0]["content"][0]["type"] == "text"

    def test_strip_last_image_multiple_msgs(self):
        from keeprollming.endpoints.streaming_handlers import _strip_last_image_url
        msgs = [
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
            {"role": "tool", "content": [dict(_TYPICAL_IMAGE_URL)]},
        ]
        result = _strip_last_image_url(msgs)
        assert result == 1
        # Last message's image should be replaced, others unchanged
        assert msgs[0]["content"][0]["type"] == "image_url"
        assert msgs[1]["content"][0]["type"] == "image_url"
        assert msgs[2]["content"][0]["type"] == "text"

    def test_strip_last_image_multiple_per_msg(self):
        from keeprollming.endpoints.streaming_handlers import _strip_last_image_url
        msgs = [{"role": "tool", "content": [
            dict(_TYPICAL_IMAGE_URL),
            dict(_TYPICAL_IMAGE_URL),
        ]}]
        result = _strip_last_image_url(msgs)
        assert result == 1
        assert msgs[0]["content"][0]["type"] == "image_url"
        assert msgs[0]["content"][1]["type"] == "text"

    def test_strip_last_image_no_images(self):
        from keeprollming.endpoints.streaming_handlers import _strip_last_image_url
        msgs = [{"role": "user", "content": "Hello"}]
        result = _strip_last_image_url(msgs)
        assert result == 0

    def test_strip_last_image_mixed_content(self):
        from keeprollming.endpoints.streaming_handlers import _strip_last_image_url
        msgs = [{"role": "tool", "content": [
            {"type": "text", "text": "Result:"},
            dict(_TYPICAL_IMAGE_URL),
            {"type": "text", "text": "End"},
        ]}]
        result = _strip_last_image_url(msgs)
        assert result == 1
        # Text items preserved, image replaced
        assert msgs[0]["content"][0]["type"] == "text"
        assert msgs[0]["content"][1]["type"] == "text"
        assert msgs[0]["content"][2]["type"] == "text"
