"""Formatter subsystem for KRM observability.

Defines the abstract ``Formatter`` base class and three concrete
implementations:

- ``JsonFormatter`` — JSON-serialized event (one line per event)
- ``PlainTextFormatter`` — Human-readable plain text format
- ``CompactFormatter`` — Minimal format for HTTP compact sink

All formatters are **stateless** and **never raise**.
"""

from __future__ import annotations

import json
import hashlib
import time
import textwrap
from abc import ABC, abstractmethod
from typing import Any

from .events import RuntimeEvent

FORMAT_ERROR = "[FORMAT_ERROR]"
_LARGE_PAYLOAD_THRESHOLD = 200


class Formatter(ABC):
    """Abstract base class for observability event formatters.

    Contract:
    - Formatters are stateless (no mutable state)
    - Formatters never raise (catch and return "[FORMAT_ERROR]")
    - Formatters receive RuntimeEvent, return str
    - No event mutation by formatters
    """

    @abstractmethod
    def format(self, event: RuntimeEvent) -> str:
        """Format a RuntimeEvent into a string representation.

        Parameters
        ----------
        event:
            The RuntimeEvent to format.

        Returns
        -------
        str
            Formatted string representation of the event.
        """
        ...


class JsonFormatter(Formatter):
    """JSON-serialized event formatter.

    Produces one JSON line per event with all envelope fields
    plus the data payload. Timestamps are converted to
    millisecond-precision epoch floats for readability.
    """

    def format(self, event: RuntimeEvent) -> str:
        """Format event as a single JSON line.

        Returns "[FORMAT_ERROR]" on any serialization failure.
        """
        try:
            payload = {
                "type": event.type,
                "source": {
                    "domain": event.source.domain,
                    "component": event.source.component,
                    "instance": event.source.instance,
                },
                "timestamp_ms": event.timestamp_ns / 1_000_000,
                "req_id": event.req_id,
                "level": event.level,
                "trace_id": event.trace_id,
                "span_id": event.span_id,
                "data": _json_safe_event_data(event.data),
            }
            return json.dumps(payload, default=_json_default)
        except Exception:
            return FORMAT_ERROR


def _json_safe_event_data(value: Any) -> Any:
    """Project raw transport bytes as metadata, never as generic JSONL data."""
    if isinstance(value, bytes):
        return {
            "_binary_omitted": True,
            "byte_length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, dict):
        return {str(key): _json_safe_event_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_event_data(item) for item in value]
    return value


class PlainTextFormatter(Formatter):
    """Human-readable plain text formatter (D-072 §8).

    Produces structured, human-oriented output suitable for console
    or transcript logging. Covers the 11 operational categories:

    1. Request identity (req_id, timestamp, method, path)
    2. Route/model/upstream resolution
    3. Material parameters (streaming, tools, pipeline)
    4. Content transcripts (system/user/assistant messages)
    5. Tool calls and results
    6. Filter/pipeline transformations
    7. Retries and nudges
    8. Response state (finish reason, errors)
    9. Usage/tokens
    10. Material performance (latency, attempts)
    11. Errors with details

    Output is NOT RuntimeEvent JSON dumps. Large payloads are rendered
    as indented blocks for readability.
    """

    def format(self, event: RuntimeEvent) -> str:
        """Format event as human-readable plain text.

        Returns "[FORMAT_ERROR]" on any formatting failure.
        """
        try:
            ts = _ns_to_iso_ms(event.timestamp_ns)
            req_id_tag = f"[{event.req_id}]" if event.req_id else ""

            # Dispatch to specialized renderers by event type prefix
            if event.type.startswith("request.lifecycle."):
                return self._render_request_lifecycle(ts, req_id_tag, event)
            if event.type.startswith("routing.resolution."):
                return self._render_routing_resolution(ts, req_id_tag, event)
            if event.type.startswith("execution.chat."):
                return self._render_execution_chat(ts, req_id_tag, event)
            if event.type.startswith("execution.pipeline."):
                return self._render_execution_pipeline(ts, req_id_tag, event)
            if event.type.startswith("execution.accounting."):
                return self._render_execution_accounting(ts, req_id_tag, event)
            if event.type.startswith("execution.performance."):
                return self._render_execution_performance(ts, req_id_tag, event)
            if event.type.startswith("execution.streaming."):
                return self._render_execution_streaming(ts, req_id_tag, event)
            if event.type.startswith("streaming."):
                return self._render_streaming(ts, req_id_tag, event)
            if event.type.startswith("filter."):
                return self._render_filter(ts, req_id_tag, event)
            if event.type.startswith("summary."):
                return self._render_summary(ts, req_id_tag, event)
            if event.type.startswith("upstream."):
                return self._render_upstream(ts, req_id_tag, event)

            # Generic fallback for unknown event types
            return self._render_generic(ts, req_id_tag, event)
        except Exception:
            return FORMAT_ERROR

    # ── Request lifecycle renderer ───────────────────────────────

    def _render_request_lifecycle(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render request.lifecycle.* events (D-072 §8: request identity)."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        if subtype == "received":
            model = d.get("client_model", "")
            stream = d.get("stream")
            endpoint = d.get("endpoint", "")
            parts = [f"{ts} {req_id_tag} request.received"]
            if model:
                parts.append(f"model={model}")
            if stream is not None:
                parts.append(f"streaming={'true' if stream else 'false'}")
            if endpoint:
                parts.append(f"path={endpoint}")
            return " ".join(parts)

        if subtype == "completed":
            status = d.get("status")
            elapsed = d.get("elapsed_ms")
            parts = [f"{ts} {req_id_tag} request.completed"]
            if status is not None:
                parts.append(f"status={status}")
            if elapsed is not None:
                parts.append(f"latency_ms={elapsed:.1f}")
            return " ".join(parts)

        if subtype == "failed":
            error = d.get("error", "")
            status = d.get("status")
            parts = [f"{ts} {req_id_tag} request.failed"]
            if status is not None:
                parts.append(f"status={status}")
            if error:
                parts.append(f'error="{_truncate(error, 120)}"')
            return " ".join(parts)

        if subtype == "cancelled":
            reason = d.get("reason", "")
            parts = [f"{ts} {req_id_tag} request.cancelled"]
            if reason:
                parts.append(f'reason="{reason}"')
            return " ".join(parts)

        # Generic lifecycle event
        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    # ── Routing resolution renderer ──────────────────────────────

    def _render_routing_resolution(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render routing.resolution.* events (D-072 §8: route/model/upstream)."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        if subtype == "resolved":
            route = d.get("resolved_route", "")
            model = d.get("upstream_model", d.get("model", ""))
            upstream = d.get("upstream_url", "")
            parts = [f"{ts} {req_id_tag} routing.resolved"]
            if route:
                parts.append(f'route="{route}"')
            if model:
                parts.append(f"model={model}")
            if upstream:
                parts.append(f"upstream={_short_url(upstream)}")
            return " ".join(parts)

        if subtype == "failed":
            model = d.get("client_model", "")
            error = d.get("error", "")
            parts = [f"{ts} {req_id_tag} routing.failed"]
            if model:
                parts.append(f"model={model}")
            if error:
                parts.append(f'error="{error}"')
            return " ".join(parts)

        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    # ── Execution chat renderer ──────────────────────────────────

    def _render_execution_chat(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render execution.chat.* events (D-072 §8: content, tools, filters, errors)."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        # Content transcripts (D-072 §8: system/user/assistant messages)
        if subtype == "conversation":
            return self._render_conversation(ts, req_id_tag, d)

        # Tool calls (D-072 §8: tool calls/results)
        if subtype == "tool_call":
            return self._render_tool_call(ts, req_id_tag, d)

        if subtype == "tool_result":
            return self._render_tool_result(ts, req_id_tag, d)

        # Assistant response (D-072 §8: content transcripts, response state)
        if subtype == "assistant":
            return self._render_assistant(ts, req_id_tag, d)

        # Route resolution details (D-072 §8: route/model/upstream)
        if subtype == "route_resolved":
            route = d.get("resolved_route", "")
            model = d.get("upstream_model", "")
            upstream = d.get("upstream_url", "")
            parts = [f"{ts} {req_id_tag} execution.route_resolved"]
            if route:
                parts.append(f'route="{route}"')
            if model:
                parts.append(f"model={model}")
            if upstream:
                parts.append(f"upstream={_short_url(upstream)}")
            return " ".join(parts)

        # HTTP in (D-072 §8: material parameters)
        if subtype == "http_in":
            model = d.get("client_model", "")
            stream = d.get("stream")
            msg_count = d.get("message_count")
            parts = [f"{ts} {req_id_tag} execution.http_in"]
            if model:
                parts.append(f"model={model}")
            if stream is not None:
                parts.append(f"streaming={'true' if stream else 'false'}")
            if msg_count is not None:
                parts.append(f"messages={msg_count}")
            return " ".join(parts)

        if subtype == "request_route":
            route = d.get("route", "")
            stream = d.get("stream")
            filters = d.get("filters", [])
            filter_text = (
                "[" + ", ".join(str(name) for name in filters) + "]"
                if isinstance(filters, list) else "[]"
            )
            parts = [f"{ts} {req_id_tag} execution.chat.request_route"]
            if stream is not None:
                parts.append(f"stream={'true' if stream else 'false'}")
            if route:
                parts.append(f'route="{route}"')
            parts.append(f"filters={filter_text}")
            return " ".join(parts)

        if subtype == "performance_metrics":
            parts = [f"{ts} {req_id_tag} execution.chat.performance_metrics"]
            for label, key in (
                ("model", "model"),
                ("route", "route_name"),
                ("elapsed_ms", "elapsed_ms"),
                ("ttft_ms", "ttft_ms"),
                ("prompt", "prompt_tokens"),
                ("cached_prompt", "cached_prompt_tokens"),
                ("uncached_prompt", "uncached_prompt_tokens"),
                ("completion", "completion_tokens"),
                ("total", "total_tokens"),
                ("tps", "completion_tps"),
                ("prompt_tps", "prompt_tps"),
                ("total_tps", "total_tps"),
                ("source", "completion_tokens_source"),
            ):
                value = d.get(key)
                if value is not None:
                    parts.append(f"{label}={_format_inline_value(value)}")
            return " ".join(parts)

        # Errors (D-072 §8: errors)
        if subtype in ("failed", "upstream_error", "pipeline_error"):
            return self._render_execution_error(ts, req_id_tag, subtype, d)

        # Filter actions / nudges (D-072 §8: filter/pipeline transformations, retries/nudges)
        if subtype == "override":
            param = d.get("param", "")
            old_val = d.get("old_value")
            new_val = d.get("new_value")
            parts = [f"{ts} {req_id_tag} execution.chat.override"]
            if param:
                parts.append(f"param={param}")
            if old_val is not None:
                parts.append(f"original={_format_inline_value(old_val)}")
            if new_val is not None:
                parts.append(f"value={_format_inline_value(new_val)}")
            return " ".join(parts)

        if subtype == "fallback":
            from_m = d.get("from_model", "")
            to_m = d.get("to_model", "")
            return f"{ts} {req_id_tag} execution.fallback {from_m}→{to_m}"

        # Generic execution event
        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    def _render_conversation(self, ts: str, req_id_tag: str, data: dict) -> str:
        """Render a message event followed by its untruncated transcript block."""
        role = str(data.get("role", "unknown"))
        text = str(data.get("text", ""))
        length = len(text)
        return self._render_transcript(
            ts, req_id_tag, "execution.chat.conversation",
            role.upper(), text, f"role={role} length={length}",
        )

    def _render_tool_call(self, ts: str, req_id_tag: str, data: dict) -> str:
        """Render complete tool calls below their canonical semantic event."""
        tool_calls = data.get("tool_calls", [])
        if not tool_calls:
            return f"{ts} {req_id_tag} execution.chat.tool_call count=0"

        lines = [f"{ts} {req_id_tag} execution.chat.tool_call count={len(tool_calls)}"]
        for tc in tool_calls:
            if isinstance(tc, dict):
                name = tc.get("name", tc.get("function", {}).get("name", "unknown"))
                tc_id = tc.get("id", "")
                args = tc.get("arguments", tc.get("function", {}).get("arguments", ""))
                args_text = str(args)
                lines.append("    TOOL_CALL:")
                lines.append(
                    f"        id={tc_id} name={name} "
                    f"arguments_length={len(args_text)}"
                )
                if args_text:
                    lines.extend(self._render_text_lines(args_text, indent="        "))
            else:
                raw = str(tc)
                lines.append("    TOOL_CALL:")
                lines.append(f"        length={len(raw)}")
                lines.extend(self._render_text_lines(raw, indent="        "))
        return "\n".join(lines)

    def _render_tool_result(self, ts: str, req_id_tag: str, data: dict) -> str:
        """Render the complete tool result below its canonical semantic event."""
        tc_id = data.get("tool_call_id", "")
        name = data.get("name", "")
        # ``emit_tool_result`` stores the payload as ``content``; retain
        # ``result`` as compatibility for older direct event emitters.
        result = str(data.get("content", data.get("result", "")))
        metadata = []
        if tc_id:
            metadata.append(f"tool_call_id={tc_id}")
        if name:
            metadata.append(f"name={name}")
        metadata.append(f"length={len(result)}")
        header = f"{ts} {req_id_tag} execution.chat.tool_result"
        lines = [" ".join([header, *metadata])]
        lines.append("    TOOL_RESULT:")
        lines.extend(self._render_text_lines(result, indent="        "))
        return "\n".join(lines)

    def _render_assistant(self, ts: str, req_id_tag: str, data: dict) -> str:
        """Render reasoning followed by the final assistant response."""
        content = data.get("content", "")
        total_len = data.get("total_length", 0)
        tool_calls = data.get("tool_calls", [])
        reasoning = data.get("reasoning_content", "")
        finish_reason = data.get("finish_reason")

        parts = [f"{ts} {req_id_tag} execution.chat.assistant"]
        parts.append(f"length={total_len}")
        if tool_calls:
            parts.append(f"tool_calls={len(tool_calls)}")
        if reasoning:
            parts.append("has_reasoning=true")
        if finish_reason:
            parts.append(f"finish_reason={finish_reason}")

        lines = [" ".join(parts)]
        if reasoning:
            lines.append("    REASONING:")
            lines.extend(self._render_text_lines(reasoning, indent="        "))
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            name = tool_call.get("name", tool_call.get("function", {}).get("name", "unknown"))
            call_id = tool_call.get("id", "")
            arguments = tool_call.get("arguments", tool_call.get("function", {}).get("arguments", ""))
            arguments_text = str(arguments)
            lines.append("    TOOL_CALL:")
            lines.append(
                f"        id={call_id} name={name} "
                f"arguments_length={len(arguments_text)}"
            )
            lines.extend(self._render_text_lines(arguments_text, indent="        "))
        # A turn ending in tool_calls has no final natural-language response.
        # Omitting an empty ASSISTANT block keeps the transcript truthful.
        if content or not tool_calls:
            lines.append("    ASSISTANT:")
            lines.extend(self._render_text_lines(content, indent="        "))
        return "\n".join(lines)

    @staticmethod
    def _render_transcript(
        ts: str, req_id_tag: str, event_name: str, label: str, text: str,
        metadata: str,
    ) -> str:
        """Render an event line and an indented, full-text transcript block."""
        lines = [f"{ts} {req_id_tag} {event_name} {metadata}".rstrip()]
        lines.append(f"    {label}:")
        lines.extend(PlainTextFormatter._render_text_lines(text, indent="        "))
        return "\n".join(lines)

    @staticmethod
    def _render_text_lines(text: str, *, indent: str) -> list[str]:
        """Wrap full transcript text while preserving its structural indent."""
        from ..logging.constants import LOG_PLAIN_WRAP_WIDTH

        width = LOG_PLAIN_WRAP_WIDTH - len(indent) if LOG_PLAIN_WRAP_WIDTH > 0 else 0
        lines: list[str] = []
        for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if width <= 0 or len(raw_line) <= width:
                lines.append(f"{indent}{raw_line}")
                continue
            wrapped = textwrap.wrap(
                raw_line, width=width, replace_whitespace=False,
                drop_whitespace=False, break_long_words=False,
                break_on_hyphens=False,
            )
            lines.extend(f"{indent}{line}" for line in (wrapped or [raw_line]))
        return lines

    def _render_execution_error(self, ts: str, req_id_tag: str, subtype: str, data: dict) -> str:
        """Render execution error events with details."""
        parts = [f"{ts} {req_id_tag} execution.{subtype}"]
        error = data.get("error", data.get("message", ""))
        status = data.get("status")
        upstream = data.get("upstream_url", data.get("url", ""))
        route = data.get("route", "")
        model = data.get("model", "")
        traceback = data.get("traceback", "")

        if status is not None:
            parts.append(f"status={status}")
        if route:
            parts.append(f'route="{route}"')
        if model:
            parts.append(f"model={model}")
        if upstream:
            parts.append(f"upstream={_short_url(upstream)}")
        if error:
            parts.append(f'error="{_truncate(error, 120)}"')

        line = " ".join(parts)
        if traceback:
            lines = [line]
            for tline in traceback.split("\n")[:10]:
                lines.append(f"    {tline.strip()}")
            if len(traceback.split("\n")) > 10:
                lines.append("    ...")
            return "\n".join(lines)
        return line

    # ── Execution pipeline renderer ──────────────────────────────

    def _render_execution_streaming(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render rate-limited live streaming telemetry."""
        d = event.data or {}
        if event.type == "execution.streaming.progress":
            parts = [f"{ts} {req_id_tag} execution.streaming.progress"]
            for key in ("elapsed_ms", "ttft_ms", "chunks", "output_chars", "output_tokens_est", "decode_tps_est"):
                value = d.get(key)
                if key == "decode_tps_est" and value is None:
                    parts.append("decode_tps_est=unavailable")
                elif value is not None:
                    parts.append(f"{key}={value}")
            return " ".join(parts)
        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    def _render_execution_pipeline(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render execution.pipeline.* events (D-072 §8: filter actions, retries/nudges)."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        # Filter triggered (D-072 §8: filter/pipeline transformations)
        if subtype == "filter_triggered":
            action = d.get("action", "")
            filter_name = d.get("filter", "")
            detail = d.get("detail", "")
            parts = [f"{ts} {req_id_tag} pipeline.filter_triggered"]
            if filter_name:
                parts.append(f"filter={filter_name}")
            if action:
                parts.append(f'action="{action}"')
            if detail:
                parts.append(f'detail="{_truncate(detail, 100)}"')
            return " ".join(parts)

        # Retry / nudge (D-072 §8: retries/nudges)
        if subtype == "stream_retry":
            filter_name = d.get("filter", "")
            delay = d.get("delay_ms")
            parts = [f"{ts} {req_id_tag} pipeline.retry"]
            if filter_name:
                parts.append(f"filter={filter_name}")
            if delay is not None:
                parts.append(f"delay_ms={delay}")
            return " ".join(parts)

        # Stream stop (filter intervention)
        if subtype == "stream_stop":
            filter_name = d.get("filter", "")
            detail = d.get("detail", "")
            parts = [f"{ts} {req_id_tag} pipeline.stream_stop"]
            if filter_name:
                parts.append(f"filter={filter_name}")
            if detail:
                parts.append(f'detail="{detail}"')
            return " ".join(parts)

        # Errors
        if subtype in ("process_request_error", "process_response_error", "stream_filter_error", "phase4_error"):
            filter_name = d.get("filter", "")
            error = d.get("error", "")
            parts = [f"{ts} {req_id_tag} pipeline.{subtype}"]
            if filter_name:
                parts.append(f"filter={filter_name}")
            if error:
                parts.append(f'error="{_truncate(error, 100)}"')
            return " ".join(parts)

        # Generic pipeline event
        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    # ── Execution accounting renderer ────────────────────────────

    def _render_execution_accounting(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render execution.accounting.* events (D-072 §8: usage/tokens)."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        if subtype == "captured":
            prompt = d.get("prompt_tokens")
            completion = d.get("completion_tokens")
            total = d.get("total_tokens")
            parts = [f"{ts} {req_id_tag} accounting.usage"]
            if prompt is not None:
                parts.append(f"prompt={prompt}")
            if completion is not None:
                parts.append(f"completion={completion}")
            if total is not None:
                parts.append(f"total={total}")
            return " ".join(parts)

        if subtype == "attempt_recorded":
            model = d.get("model", "")
            attempt = d.get("attempt")
            tokens = d.get("tokens")
            parts = [f"{ts} {req_id_tag} accounting.attempt"]
            if model:
                parts.append(f"model={model}")
            if attempt is not None:
                parts.append(f"attempt={attempt}")
            if tokens is not None:
                parts.append(f"tokens={tokens}")
            return " ".join(parts)

        if subtype == "finalized":
            attempts = d.get("total_attempts")
            cost = d.get("total_cost")
            parts = [f"{ts} {req_id_tag} accounting.finalized"]
            if attempts is not None:
                parts.append(f"attempts={attempts}")
            if cost is not None:
                parts.append(f"cost={cost:.4f}")
            return " ".join(parts)

        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    def _render_execution_performance(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render the single terminal BASIC operational summary."""
        d = event.data or {}
        parts = [f"{ts} {req_id_tag} USAGE"]
        usage_missing = (
            d.get("upstream_attempts", 0) > 0
            and d.get("usage_reported_attempts", 0) == 0
        )
        if usage_missing:
            parts.extend(("prompt=?", "completion=?", "total=?", "usage=unavailable"))
        else:
            for label, key in (("prompt", "prompt_tokens"), ("completion", "completion_tokens"),
                               ("total", "total_tokens")):
                value = d.get(key)
                if value is not None:
                    parts.append(f"{label}={value}")
        for label, key in (("finish_reason", "finish_reason"), ("elapsed_ms", "elapsed_ms"),
                           ("upstream_attempts", "upstream_attempts"),
                           ("usage_reported_attempts", "usage_reported_attempts")):
            value = d.get(key)
            if value is not None:
                parts.append(f"{label}={value}")
        if d.get("usage_complete") is not None:
            parts.append(f"usage_complete={d['usage_complete']}")
        return " ".join(parts)

    # ── Streaming renderer ───────────────────────────────────────

    def _render_streaming(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render streaming.* events."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        if subtype == "event":
            event_type = d.get("event_type", "")
            delta_len = d.get("delta_len")
            parts = [f"{ts} {req_id_tag} streaming.event"]
            if event_type:
                parts.append(f"type={event_type}")
            if delta_len is not None:
                parts.append(f"delta_len={delta_len}")
            # Include remaining data fields
            for k, v in d.items():
                if k not in ("event_type", "delta_len"):
                    if isinstance(v, str):
                        parts.append(f'{k}="{self._escape_short(v)}"')
                    elif isinstance(v, (dict, list)):
                        parts.append(f"{k}=[{len(v)} items]")
                    else:
                        parts.append(f"{k}={v}")
            return " ".join(parts)

        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    # ── Filter renderer ──────────────────────────────────────────

    def _render_filter(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render filter.* events (D-072 §8: filter/pipeline transformations)."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        if subtype == "executed":
            filters = d.get("filters", [])
            status = d.get("status", "")
            parts = [f"{ts} {req_id_tag} filter.executed"]
            if filters:
                parts.append(f"filters={','.join(filters)}")
            if status:
                parts.append(f"status={status}")
            return " ".join(parts)

        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    # ── Summary renderer ─────────────────────────────────────────

    def _render_summary(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render summary.* events."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        if subtype == "started":
            reason = d.get("reason", "")
            parts = [f"{ts} {req_id_tag} summary.started"]
            if reason:
                parts.append(f'reason="{reason}"')
            return " ".join(parts)

        if subtype == "completed":
            tokens_before = d.get("tokens_before")
            tokens_after = d.get("tokens_after")
            parts = [f"{ts} {req_id_tag} summary.completed"]
            if tokens_before is not None:
                parts.append(f"before={tokens_before}")
            if tokens_after is not None:
                parts.append(f"after={tokens_after}")
            return " ".join(parts)

        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    # ── Upstream renderer ────────────────────────────────────────

    def _render_upstream(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Render upstream.* events (D-072 §8: material performance)."""
        d = event.data or {}
        subtype = event.type.split(".")[-1]

        if subtype == "request":
            url = d.get("url", "")
            model = d.get("model", "")
            parts = [f"{ts} {req_id_tag} upstream.request"]
            if url:
                parts.append(f"url={_short_url(url)}")
            if model:
                parts.append(f"model={model}")
            return " ".join(parts)

        if subtype == "response":
            status = d.get("status")
            latency = d.get("latency_ms")
            parts = [f"{ts} {req_id_tag} upstream.response"]
            if status is not None:
                parts.append(f"status={status}")
            if latency is not None:
                parts.append(f"latency_ms={latency:.1f}")
            return " ".join(parts)

        return self._render_generic_kv(ts, req_id_tag, event.type, d)

    # ── Generic fallback renderer ────────────────────────────────

    def _render_generic(self, ts: str, req_id_tag: str, event: RuntimeEvent) -> str:
        """Generic fallback for unknown event types."""
        return self._render_generic_kv(ts, req_id_tag, event.type, event.data or {})

    def _render_generic_kv(self, ts: str, req_id_tag: str, event_type: str, data: dict) -> str:
        """Render generic event with key=value pairs."""
        if not data:
            return f"{ts} {req_id_tag} {event_type}"

        parts = [f"{ts} {req_id_tag} {event_type}"]
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 150:
                parts.append(f'{k}="{_truncate(v, 120)}..."')
            elif isinstance(v, str):
                parts.append(f'{k}="{self._escape_short(v)}"')
            elif isinstance(v, (dict, list)):
                parts.append(f"{k}=[{len(v)} items]")
            else:
                parts.append(f"{k}={v}")
        return " ".join(parts)

    # ── Internal helpers ─────────────────────────────────────────

    def _escape_short(self, s: str) -> str:
        """Escape a short string for inline display."""
        return s.replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


class CompactFormatter(Formatter):
    """Minimal format formatter for HTTP compact sink.

    Produces a single-line, space-separated format:
    ``LEVEL type source ns timestamp_ms req_id``
    with data omitted for maximum compactness.
    """

    def format(self, event: RuntimeEvent) -> str:
        """Format event as a compact single line.

        Returns "[FORMAT_ERROR]" on any formatting failure.
        """
        try:
            d = event.data or {}
            ts_ms = event.timestamp_ns / 1_000_000
            parts = [f"ts_ms={ts_ms:.3f}", "method=POST", "path=/v1/chat/completions"]
            if event.req_id:
                parts.append(f"req_id={event.req_id}")
            for key in ("route_name", "model", "elapsed_ms", "finish_reason", "upstream_attempts",
                        "prompt_tokens", "completion_tokens", "total_tokens"):
                value = d.get(key)
                if value is not None:
                    parts.append(f"{key}={value}")
            return " ".join(parts)
        except Exception:
            return FORMAT_ERROR


# ── Internal helpers ──────────────────────────────────────────────


def _json_default(obj: Any) -> Any:
    """Handle non-serializable objects in JSON output."""
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _ns_to_iso_ms(timestamp_ns: int) -> str:
    """Convert nanosecond timestamp to ISO-like string with milliseconds."""
    try:
        seconds = timestamp_ns / 1_000_000_000
        ms = (timestamp_ns % 1_000_000_000) // 1_000_000
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{ms:03d}Z"
    except Exception:
        return str(timestamp_ns)


def _ns_to_readable(timestamp_ns: int) -> str:
    """Convert nanosecond timestamp to human-readable ISO-like string.

    Kept for backward compatibility.
    """
    try:
        seconds = timestamp_ns / 1_000_000_000
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds))
    except Exception:
        return str(timestamp_ns)


def _truncate(s: str, max_len: int) -> str:
    """Truncate a string to max_len characters."""
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


def _format_inline_value(value: Any) -> str:
    """Serialize a short operational value without losing its type."""
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(value)


def _short_url(url: str) -> str:
    """Shorten a URL for display (keep host + path prefix)."""
    if not url:
        return ""
    try:
        # Simple parsing without importing urllib
        if url.startswith("http://"):
            url = url[7:]
        elif url.startswith("https://"):
            url = url[8:]
        # Keep host and first path segment
        parts = url.split("/", 1)
        host = parts[0]
        path = "/" + parts[1][:30] if len(parts) > 1 else ""
        return host + path
    except Exception:
        return _truncate(url, 50)
