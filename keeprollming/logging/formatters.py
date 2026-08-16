"""Formatting utilities for structured log output (ANSI-colored)."""

from __future__ import annotations

import json
import re
import textwrap
from typing import Any

from .constants import ANSI_BOLD, ANSI_BLUE, ANSI_CYAN, ANSI_DIM, ANSI_GREEN, ANSI_MAGENTA, ANSI_RED, ANSI_YELLOW, ANSI_RESET


def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI color codes (uses LOG_PLAIN_COLORS from constants)."""
    from .constants import LOG_PLAIN_COLORS
    if not LOG_PLAIN_COLORS or not codes:
        return text
    return "".join(codes) + text + ANSI_RESET


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from a string."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ── Speaker highlighting & line wrapping ───────────────────────────

SPEAKER_COLORS = (
    ("AI:", ANSI_GREEN),
    ("Human:", ANSI_CYAN),
    ("USER:", ANSI_CYAN),
    ("ASSISTANT:", ANSI_GREEN),
)


def _highlight_speaker_chunk(line: str) -> str:
    """Highlight speaker prefixes in a log line."""
    for speaker, color in SPEAKER_COLORS:
        if line.startswith(speaker):
            return _c(speaker, ANSI_BOLD, color) + line[len(speaker):]
    return line


def _wrap_plain_line(line: str, *, available_width: int) -> list[str]:
    """Wrap a single log line to the given width."""
    from .constants import LOG_PLAIN_WRAP_WIDTH as wrap  # noqa: F811

    if available_width <= 0 or wrap <= 0:
        return [line]
    if len(_strip_ansi(line)) <= available_width:
        return [line]

    speaker_prefix = ""
    m = re.match(r"^(AI:|Human:|USER:|ASSISTANT:)(\s*)", line)
    if m:
        speaker_prefix = " " * len(_strip_ansi(m.group(0)))

    wrapped = textwrap.wrap(
        line,
        width=available_width,
        replace_whitespace=False,
        drop_whitespace=False,
        break_long_words=False,
        break_on_hyphens=False,
        subsequent_indent=speaker_prefix,
    )
    return wrapped or [line]


def _indent_block(text: str | None, prefix: str = "│   ") -> list[str]:
    """Indent a multi-line text block with the given prefix. Returns list of lines."""
    from .constants import LOG_PLAIN_WRAP_WIDTH as wrap_width  # noqa: F811

    if not text:
        return [prefix.rstrip()]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    available_width = max(0, wrap_width - len(_strip_ansi(prefix)))
    out: list[str] = []
    for line in lines:
        for wrapped in _wrap_plain_line(line, available_width=available_width):
            out.append(prefix + _highlight_speaker_chunk(wrapped))
    return out if out else [prefix.rstrip()]


# ── Meta / token / usage formatters ────────────────────────────────

def _fmt_meta_item(key: str, value: Any) -> str:
    """Format a single metadata key=value pair."""
    return f"{_c(key, ANSI_DIM, ANSI_CYAN)}={_c(str(value), ANSI_BOLD, ANSI_YELLOW)}"


def _fmt_meta(**kwargs: Any) -> str:
    """Format multiple metadata fields as space-separated key=val pairs."""
    return " ".join(_fmt_meta_item(k, v) for k, v in kwargs.items() if v is not None)


def _fmt_tokens(prompt: int | None, completion: int | None, total: int | None = None) -> str:
    """Format token counts as 'prompt/completion/total' or 'prompt/completion'."""
    parts = []
    if prompt is not None:
        parts.append(str(prompt))
    else:
        parts.append("-")
    if completion is not None:
        parts.append(str(completion))
    else:
        parts.append("-")
    if total is not None:
        parts.append(f"total={total}")
    return "/".join(parts)


def _normalize_summary_text(value: Any) -> str:
    """Normalize summary text (handle JSON-encoded strings)."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, str):
                value = parsed
        except Exception:
            pass
        return value.replace("\r\n", "\n").replace("\r", "\n")
    return str(value)


def _fmt_usage(usage: Any) -> str:
    """Format a usage dict as 'prompt=X, completion=Y, total=Z'."""
    if not isinstance(usage, dict):
        return "-"
    p = usage.get("prompt_tokens")
    c = usage.get("completion_tokens")
    t = usage.get("total_tokens")
    parts = []
    if p is not None:
        parts.append(f"prompt={p}")
    if c is not None:
        parts.append(f"completion={c}")
    if t is not None:
        parts.append(f"total={t}")
    return ", ".join(parts) if parts else "-"


# ── Tool call / tool result formatters ─────────────────────────────

def _fmt_tool_calls_yaml(tool_calls: list[Any]) -> str | None:
    """Format tool calls as YAML-style list with one line per call."""
    from .constants import LOG_PLAIN_COLORS, ANSI_RESET

    if not isinstance(tool_calls, list) or not tool_calls:
        return None

    lines = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue

        func = tc.get("function", {})
        if not isinstance(func, dict) or not func.get("name"):
            lines.append(f"  <raw: {json.dumps(tc, default=str)}")
            continue

        name = func.get("name", "unknown")
        args = func.get("arguments", "")

        # Parse JSON arguments if present
        if isinstance(args, str) and args.strip():
            try:
                args_dict = json.loads(args)
                arg_parts = []
                for k, v in args_dict.items():
                    if isinstance(v, (dict, list)):
                        arg_parts.append(_c(f'{k}={json.dumps(v)}', ANSI_DIM, ANSI_BLUE))
                    else:
                        arg_parts.append(f"{_c(k, ANSI_YELLOW)}=\"{_c(str(v), ANSI_GREEN)}\"")
                args_str = ", ".join(arg_parts)
            except Exception:
                args_str = _c(args, ANSI_DIM, ANSI_BLUE) if args else ""
        else:
            args_str = _c(str(args), ANSI_DIM, ANSI_BLUE) if args else ""

        if args_str:
            lines.append(f"  {_c('•', ANSI_MAGENTA)} {_c(name, ANSI_CYAN)}({args_str})")
        else:
            lines.append(f"  {_c('•', ANSI_MAGENTA)} {_c(name, ANSI_CYAN)}")

    return "\n".join(lines) if lines else None


def _fmt_tool_result_yaml(tool_call_id: str | None, name: str | None, result: Any) -> str | None:
    """Format tool result with YAML-style formatting."""
    if result is None:
        return None

    header_parts = []
    if name:
        header_parts.append(_c(name, ANSI_CYAN))
    if tool_call_id:
        header_parts.append(f"[{tool_call_id}]")
    header = " ".join(header_parts) if header_parts else "TOOL_RESULT"

    # Format result based on type - compact inline representation avoids wrapping issues.
    if isinstance(result, (dict, list)):
        try:
            formatted_result = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
            colored_result = _c(formatted_result, ANSI_DIM, ANSI_BLUE)
            return f"{header} {colored_result}"
        except Exception:
            result_str = str(result)
    elif isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, (dict, list)):
                formatted_result = json.dumps(parsed, ensure_ascii=False, separators=(',', ':'))
                colored_result = _c(formatted_result, ANSI_DIM, ANSI_BLUE)
                return f"{header} {colored_result}"
        except Exception:
            pass
        result_str = result
    else:
        result_str = str(result)

    return f"{header} {_c(result_str, ANSI_YELLOW)}" if isinstance(result_str, str) and result_str else None


# ── Plain-text request header/footer ───────────────────────────────

def _plain_header(req_id: str) -> str:
    """Format the opening marker for a plain-text log entry."""
    return _c(f"┌─ REQUEST {req_id}", ANSI_BOLD, ANSI_CYAN)


def _plain_footer(req_id: str) -> str:
    """Format the closing marker for a plain-text log entry."""
    return _c(f"└─ END {req_id}", ANSI_DIM, ANSI_GRAY)
