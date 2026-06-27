import json

import pytest

import keeprollming.logger as logger_mod


def test_basic_plain_summary_reply_unescapes_newlines_and_keeps_indent(monkeypatch):
    from keeprollming.logging import constants as logging_constants

    monkeypatch.setattr(logger_mod, "LOG_MODE", "BASIC_PLAIN")
    monkeypatch.setattr(logging_constants, "LOG_PLAIN_COLORS", False)
    monkeypatch.setattr(logger_mod, "_PLAIN_LAST_REQ_ID", None)
    monkeypatch.setattr(logger_mod, "_PLAIN_CLOSED_REQ_IDS", set())

    rendered = logger_mod._format_plain({
        "msg": "summary_reply",
        "req_id": "sum-1",
        "elapsed_ms": 99.9,
        "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33},
        "summary_snip": json.dumps("line one\nline two\n[line three]"),
    })

    assert '\nline two' not in rendered
    assert '"line one' not in rendered
    assert "│   line one" in rendered
    assert "│   line two" in rendered
    assert "│   [line three]" in rendered
