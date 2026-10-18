"""Opt-in file removal protects the full conversation without discarding images."""

from copy import deepcopy
from types import SimpleNamespace as Request

import pytest

from keeprollming.filters import validate_filter_module_settings
from keeprollming.filters.multimodal_validator.request import MultimodalValidatorFilter
from keeprollming.orchestrator.filter import FilterExecutionContext
from keeprollming.orchestrator.pipeline import Pipeline

IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,IMAGE"}}
FILE = {"type": "file", "file": {"filename": "report.pdf", "file_data": "data:application/pdf;base64,PDF"}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "part",
    [
        FILE,
        {"type": "input_file", "file_id": "file-123"},
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "PDF"}},
        {"type": "file_url", "file_url": "https://example.invalid/report.docx"},
        {"type": "document_url", "document_url": "https://example.invalid/report.pdf"},
        {"type": "image_url", "image_url": {"url": "data:application/pdf;base64,PDF"}},
        {"type": "input_image", "image_url": "data:APPLICATION/PDF;base64,PDF"},
        {"type": "image", "source": {"media_type": "application/pdf", "data": "PDF"}},
    ],
)
async def test_structured_files_and_pdf_image_parts_are_replaced(part):
    original = {"role": "user", "content": [{"type": "text", "text": "Read this"}, deepcopy(part), IMAGE]}
    request = Request(messages=[deepcopy(original)], model="test")
    filter_ = MultimodalValidatorFilter({"strip_all_files": True, "files_replacement_text": "[Attachment removed]"})
    result = await filter_.process_request(request, FilterExecutionContext(req_id="files-test"))
    assert result.messages[0]["content"] == [
        original["content"][0],
        {"type": "text", "text": "[Attachment removed]"},
        IMAGE,
    ]
    assert original["content"][1] == part


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [{}, {"strip_all_files": False}, {"strip_all_files": True, "enabled": False}])
async def test_default_off_and_disabled_leave_attachments_unchanged(settings):
    message = {"role": "user", "content": [deepcopy(FILE), IMAGE]}
    request = Request(messages=[deepcopy(message)], model="test")
    result = await MultimodalValidatorFilter(settings).process_request(request, FilterExecutionContext(req_id="off"))
    assert result.messages == [message]


@pytest.mark.asyncio
async def test_file_only_messages_keep_their_identity_across_all_roles_and_history():
    events = []

    class Capture:
        def emit(self, event):
            events.append(event)

    messages = [
        {"role": role, "content": [deepcopy(FILE)], "tool_call_id": "call-123"}
        for role in ("system", "user", "assistant", "tool")
    ]
    context = FilterExecutionContext(req_id="files-history", event_dispatcher=Capture())
    result = await MultimodalValidatorFilter({"strip_all_files": True}).process_request(
        Request(messages=messages, model="test"), context
    )
    for message in result.messages:
        assert message["tool_call_id"] == "call-123"
        assert message["content"] == [{"type": "text", "text": "[File omitted: attachments are disabled]"}]
    assert len(events) == 1
    assert events[0].type == "filter.multimodal_validator.files_stripped"
    assert events[0].req_id == "files-history" and events[0].level == "BASIC"
    assert events[0].data == {"stripped": 4}


@pytest.mark.asyncio
async def test_does_not_inspect_plain_text_tool_arguments_or_infer_pdf_from_url_name():
    messages = [
        {"role": "tool", "tool_call_id": "call-1", "content": 'Extracted PDF text: {"type":"file"}'},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "Read the PDF",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"report.pdf"}'},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "report.pdf"},
                {"type": "image_url", "image_url": {"url": "https://example.invalid/report.pdf"}},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,IMAGE"},
            ],
        },
    ]
    original = deepcopy(messages)
    result = await MultimodalValidatorFilter({"strip_all_files": True}).process_request(
        Request(messages=messages, model="test"), FilterExecutionContext(req_id="text")
    )
    assert result.messages == original


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_pipeline_sanitizes_files_before_either_serving_mode(stream):
    pipeline = Pipeline.from_route_config(
        {
            "multimodal_validator": {
                "enabled": True,
                "strip_all_files": True,
                "strip_orphaned_markers": False,
                "max_images": 1,
            }
        }
    )
    payload = {
        "model": "test",
        "stream": stream,
        "messages": [
            {"role": "user", "content": [deepcopy(FILE), IMAGE]},
            {"role": "tool", "tool_call_id": "old-call", "content": [deepcopy(FILE)]},
        ],
    }
    result = await pipeline.process_request(payload, req_id="pipeline-files", upstream_model="test")
    assert result["stream"] is stream
    assert result["messages"][0]["content"][1] == IMAGE
    assert result["messages"][1]["tool_call_id"] == "old-call"
    assert all(part["type"] in {"text", "image_url"} for message in result["messages"] for part in message["content"])


@pytest.mark.asyncio
async def test_opt_out_preserves_file_during_orphaned_marker_cleanup():
    request = Request(messages=[{"role": "user", "content": [{"type": "text", "text": "<image>"}, FILE]}], model="test")
    result = await MultimodalValidatorFilter().process_request(request, FilterExecutionContext(req_id="keep-file"))
    assert result.messages[0]["content"] == [FILE]


@pytest.mark.parametrize("settings", [{"strip_all_files": "true"}, {"files_replacement_text": 1}])
def test_file_settings_are_validated_at_startup(settings):
    with pytest.raises(ValueError):
        validate_filter_module_settings({"multimodal_validator": settings})


def test_valid_file_settings_are_accepted():
    validate_filter_module_settings(
        {"multimodal_validator": {"strip_all_files": True, "files_replacement_text": "[removed]"}}
    )
