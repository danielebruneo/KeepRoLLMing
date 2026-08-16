"""OpenAI-compatible SSE serializer for Streaming canonical streaming pipeline.

Converts canonical ``StreamEvent`` objects into downstream SSE bytes
that are parseable by ``tests/helpers/stream_client.py``.

Event support matrix (canonical skeleton):

  Fully supported (core path):
    - ``AssistantTextDelta`` -> ``choices[0].delta.content``
    - ``Finish`` -> ``choices[0].finish_reason`` (delta is ``{}``)
    - ``Done`` -> ``data: [DONE]``

  Supported (spec-defined, tested):
    - ``ReasoningTextDelta`` -> ``choices[0].delta.reasoning_content``
    - ``ToolCallDelta`` -> ``choices[0].delta.tool_calls`` (incremental)
    - ``ToolCallComplete`` -> ``choices[0].delta.tool_calls`` (complete)
    - ``Keepalive`` -> SSE comment ``: keepalive``

  Partial support:
    - ``Error`` -> ``data: {"error": {...}}`` (no ``choices``; silently
      skipped by ``stream_client``'s JSON parser)

  Unsupported:
    - Unknown ``StreamEvent`` subclasses -> ``ValueError``
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterable, List

from .events import (
    AssistantTextDelta,
    Done,
    Error,
    Finish,
    Keepalive,
    ReasoningTextDelta,
    StreamEvent,
    ToolCallComplete,
    ToolCallDelta,
)

# Lazy import to avoid circular dependency
try:
    from ..observability.events import EventSource, RuntimeEvent
    _OBSERVABILITY_AVAILABLE = True
except ImportError:
    _OBSERVABILITY_AVAILABLE = False


# ---------------------------------------------------------------------------
# SSE frame helpers
# ---------------------------------------------------------------------------

def _sse_frame(data: str) -> bytes:
    """Build a single SSE data frame: ``data: <payload>\\n\\n``.

    The payload is JSON-escaped when it looks like a JSON object.
    """
    return f"data: {data}\n\n".encode("utf-8")


def _json_sse_payload(obj: object) -> str:
    """Serialise *obj* to a JSON string suitable for an SSE ``data:`` line."""
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Per-event serializers
# ---------------------------------------------------------------------------

def _serialize_assistant_text_delta(event: AssistantTextDelta) -> bytes:
    """``AssistantTextDelta`` -> SSE with ``choices[0].delta.content``."""
    payload = {
        "id": event.event_id or "",
        "object": "chat.completion.chunk",
        "created": event.metadata.get("created", 0),
        "model": event.metadata.get("model", ""),
        "choices": [
            {
                "index": 0,
                "delta": _assistant_delta(event, content=event.delta),
            }
        ],
    }
    return _sse_frame(_json_sse_payload(payload))


def _serialize_reasoning_text_delta(event: ReasoningTextDelta) -> bytes:
    """``ReasoningTextDelta`` -> SSE with ``choices[0].delta.reasoning_content``."""
    payload = {
        "id": event.event_id or "",
        "object": "chat.completion.chunk",
        "created": event.metadata.get("created", 0),
        "model": event.metadata.get("model", ""),
        "choices": [
            {
                "index": 0,
                "delta": _assistant_delta(event, reasoning_content=event.delta),
            }
        ],
    }
    return _sse_frame(_json_sse_payload(payload))


def _serialize_tool_call_delta(event: ToolCallDelta) -> bytes:
    """``ToolCallDelta`` -> SSE with ``choices[0].delta.tool_calls``."""
    # Build the tool_calls array expected by the OpenAI-compatible protocol.
    tool_call = {
        "index": event.index,
    }
    if event.id:
        tool_call["id"] = event.id
    if event.name:
        tool_call["function"] = {"name": event.name}
    if event.arguments_delta:
        tool_call["function"] = tool_call.get("function", {})
        tool_call["function"]["arguments"] = event.arguments_delta

    payload = {
        "id": event.event_id or "",
        "object": "chat.completion.chunk",
        "created": event.metadata.get("created", 0),
        "model": event.metadata.get("model", ""),
        "choices": [
            {
                "index": 0,
                "delta": _assistant_delta(event, tool_calls=[tool_call]),
            }
        ],
    }
    return _sse_frame(_json_sse_payload(payload))


def _serialize_tool_call_complete(event: ToolCallComplete) -> bytes:
    """``ToolCallComplete`` -> SSE with complete tool call in ``delta.tool_calls``.

    The arguments are emitted as a JSON string in ``function.arguments``.
    """
    tool_call = {
        "index": event.index,
        "id": event.id,
        "type": "function",
        "function": {
            "name": event.name,
            "arguments": event.arguments_json,
        },
    }

    payload = {
        "id": event.event_id or "",
        "object": "chat.completion.chunk",
        "created": event.metadata.get("created", 0),
        "model": event.metadata.get("model", ""),
        "choices": [
            {
                "index": 0,
                "delta": _assistant_delta(event, tool_calls=[tool_call]),
            }
        ],
    }
    return _sse_frame(_json_sse_payload(payload))


def _serialize_finish(event: Finish) -> bytes:
    """``Finish`` -> SSE with ``choices[0].finish_reason`` and empty delta.

    The delta is an empty dict (no ``content`` field) to avoid emitting
    assistant content after ``finish_reason`` in the same frame.
    """
    payload = {
        "id": event.event_id or "",
        "object": "chat.completion.chunk",
        "created": event.metadata.get("created", 0),
        "model": event.metadata.get("model", ""),
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": event.reason,
            }
        ],
        "usage": event.usage,
    }
    return _sse_frame(_json_sse_payload(payload))


def _serialize_done(_: Done) -> bytes:
    """``Done`` -> ``data: [DONE]\\n\\n``."""
    return b"data: [DONE]\n\n"


def _serialize_keepalive(_: Keepalive) -> bytes:
    """``Keepalive`` -> SSE comment (``: keepalive\\n\\n``)."""
    return b": keepalive\n\n"


def _serialize_error(event: Error) -> bytes:
    """``Error`` -> SSE with error payload.

    Note: ``stream_client``'s JSON parser expects ``choices`` in the
    payload. Error frames use ``{"error": {...}}`` which has no
    ``choices``, so they are silently skipped by the parser.
    """
    payload = {
        "error": {
            "message": event.message,
            "type": event.code,
        }
    }
    return _sse_frame(_json_sse_payload(payload))


def _assistant_delta(event: StreamEvent, **fields: Any) -> dict:
    """Return one semantic delta, with ``role`` only on stream start."""
    delta: dict = {}
    if event.metadata.pop("_emit_role", False):
        delta["role"] = "assistant"
    delta.update(fields)
    return delta


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_SERIALIZERS = {
    AssistantTextDelta: _serialize_assistant_text_delta,
    ReasoningTextDelta: _serialize_reasoning_text_delta,
    ToolCallDelta: _serialize_tool_call_delta,
    ToolCallComplete: _serialize_tool_call_complete,
    Finish: _serialize_finish,
    Done: _serialize_done,
    Keepalive: _serialize_keepalive,
    Error: _serialize_error,
}


def _serialize_event(event: StreamEvent) -> bytes:
    """Serialize a single ``StreamEvent`` to SSE bytes.

    Raises ``ValueError`` for unhandled event types.
    """
    handler = _SERIALIZERS.get(type(event))
    if handler is None:
        # Fallback: try the base class.
        for cls, h in _SERIALIZERS.items():
            if isinstance(event, cls):
                handler = h
                break
        if handler is None:
            raise ValueError(f"Unhandled StreamEvent type: {type(event).__name__}")
    return handler(event)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class OpenAISSESerializer:
    """Serializes canonical canonical ``StreamEvent`` objects to OpenAI-compatible SSE.

    Example usage::

        ser = OpenAISSESerializer()
        for frame in ser.serialize_events([
            AssistantTextDelta(delta="Hello"),
            Finish(reason="stop"),
            Done(),
        ]):
            yield frame  # send to client

    Parameters
    ----------
    dispatcher:
        Optional ``EventDispatcher`` for observability instrumentation.
    """

    def __init__(self, dispatcher: Any = None) -> None:
        """Initialize serializer with optional observability dispatcher.

        Parameters
        ----------
        dispatcher:
            Optional EventDispatcher instance.
        """
        self._dispatcher = dispatcher
        self._event_id = f"chatcmpl-{uuid.uuid4().hex}"
        self._metadata: dict = {"created": int(time.time())}
        self._role_emitted = False

    def serialize_event(self, event: StreamEvent) -> bytes:
        """Serialize one ``StreamEvent`` to SSE bytes."""
        self._prepare_envelope(event)
        result = _serialize_event(event)
        # O2: emit serialization event
        self._emit_serialize_event(event)
        return result

    def _prepare_envelope(self, event: StreamEvent) -> None:
        """Keep a valid, stable OpenAI envelope across transformed events."""
        if event.event_id:
            self._event_id = event.event_id
        else:
            event.event_id = self._event_id

        if event.metadata:
            self._metadata.update(
                {key: value for key, value in event.metadata.items() if not key.startswith("_")}
            )
        for key, value in self._metadata.items():
            event.metadata.setdefault(key, value)

        if isinstance(event, (AssistantTextDelta, ReasoningTextDelta, ToolCallDelta, ToolCallComplete)):
            event.metadata["_emit_role"] = not self._role_emitted
            self._role_emitted = True

    def _emit_serialize_event(self, event: StreamEvent) -> None:
        """Emit a RuntimeEvent for SSE serialization.

        O2 instrumentation: emits events at serialization points.
        No-op when dispatcher is None or observability is unavailable.
        """
        if not _OBSERVABILITY_AVAILABLE or self._dispatcher is None:
            return
        event_type = type(event).__name__
        data: dict = {"serialized_event_type": event_type}
        if isinstance(event, (AssistantTextDelta, ReasoningTextDelta)):
            data["delta_len"] = len(event.delta)
        if isinstance(event, Finish):
            data["finish_reason"] = event.reason
        if isinstance(event, (ToolCallDelta, ToolCallComplete)):
            data["tool_call_index"] = event.index
        self._dispatcher.emit(
            RuntimeEvent(
                type="streaming.serializer.serialize",
                timestamp_ns=time.time_ns(),
                source=EventSource(domain="streaming", component="serializer"),
                data=data,
                level="DEBUG",
            )
        )

    def serialize_events(self, events: Iterable[StreamEvent]) -> List[bytes]:
        """Serialize an iterable of ``StreamEvent`` objects to SSE frames.

        Returns a list of SSE frames (bytes), each ending with ``\\n\\n``.
        """
        return [self.serialize_event(e) for e in events]


def serialize_event(event: StreamEvent) -> bytes:
    """Module-level convenience: serialize one ``StreamEvent`` to SSE bytes."""
    return _serialize_event(event)


def serialize_events(events: Iterable[StreamEvent]) -> List[bytes]:
    """Module-level convenience: serialize an iterable of events to SSE frames."""
    return OpenAISSESerializer().serialize_events(events)
