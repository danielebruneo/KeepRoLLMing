"""Regression coverage for compatibility logging helpers and failure paths."""

from __future__ import annotations

import asyncio
import importlib
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import httpx


def test_log_request_parses_json_body(monkeypatch) -> None:
    logger_module = importlib.import_module("keeprollming.logger")
    captured: dict = {}
    monkeypatch.setattr(
        logger_module,
        "log",
        lambda level, message, **fields: captured.update(
            level=level,
            message=message,
            **fields,
        ),
    )
    request = httpx.Request(
        "POST",
        "http://upstream/v1/chat/completions",
        headers={"content-type": "application/json"},
        json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
    )

    asyncio.run(logger_module.log_request(request))

    assert captured["body"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_log_response_parses_httpx_json_response(monkeypatch) -> None:
    logger_module = importlib.import_module("keeprollming.logger")
    captured: dict = {}
    monkeypatch.setattr(
        logger_module,
        "log",
        lambda level, message, **fields: captured.update(
            level=level,
            message=message,
            **fields,
        ),
    )
    body = {
        "model": "test-model",
        "choices": [{"message": {"content": "hello"}}],
    }
    response = httpx.Response(
        200,
        json=body,
        request=httpx.Request("POST", "http://upstream/v1/chat/completions"),
    )

    asyncio.run(logger_module.log_response(response))

    assert captured["body"] == body
    assert captured["model"] == "test-model"


def test_server_log_file_failure_uses_bound_logger(monkeypatch) -> None:
    server_events = importlib.import_module("keeprollming.logging.server_events")
    fake_logger = Mock(spec=logging.Logger)
    fake_logger.handlers = []
    monkeypatch.setattr(server_events, "_SERVER_LOGGER", None)

    def fail_handler(*_args, **_kwargs):
        raise OSError("unwritable log directory")

    monkeypatch.setattr(
        server_events,
        "logging",
        SimpleNamespace(
            getLogger=lambda _name: fake_logger,
            handlers=SimpleNamespace(RotatingFileHandler=fail_handler),
            INFO=logging.INFO,
        ),
    )

    assert server_events.setup_server_logging() is fake_logger
    fake_logger.warning.assert_called_once()


def test_debug_log_file_failure_uses_bound_logger(monkeypatch) -> None:
    server_events = importlib.import_module("keeprollming.logging.server_events")
    fake_logger = Mock(spec=logging.Logger)
    fake_logger.handlers = []
    monkeypatch.setattr(server_events, "_DEBUG_LOGGER", None)

    def fail_handler(*_args, **_kwargs):
        raise OSError("unwritable debug log directory")

    monkeypatch.setattr(
        server_events,
        "logging",
        SimpleNamespace(
            getLogger=lambda _name: fake_logger,
            handlers=SimpleNamespace(RotatingFileHandler=fail_handler),
            DEBUG=logging.DEBUG,
        ),
    )

    assert server_events.setup_debug_logging() is fake_logger
    fake_logger.warning.assert_called_once()


def test_context_token_clamp_imports_safety_margin() -> None:
    from keeprollming.config import SAFETY_MARGIN_TOK
    from keeprollming.utils.token_utils import clamp_max_tokens_for_context

    context_length = 4096
    clamped = clamp_max_tokens_for_context(10_000, context_length)

    assert clamped == max(64, context_length - SAFETY_MARGIN_TOK - 256)


def test_plain_footer_has_gray_ansi_constant(monkeypatch) -> None:
    constants = importlib.import_module("keeprollming.logging.constants")
    formatters = importlib.import_module("keeprollming.logging.formatters")
    monkeypatch.setattr(constants, "LOG_PLAIN_COLORS", True)

    footer = formatters._plain_footer("req-123")

    assert "req-123" in footer
    assert constants.ANSI_GRAY in footer
