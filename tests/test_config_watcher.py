"""Regression coverage for config-watcher observability routing."""

from __future__ import annotations

import asyncio

import pytest

from keeprollming.observability import EventDispatcher, RuntimeEvent


def test_config_watcher_emits_reload_through_dispatcher(monkeypatch):
    """A reload is projected as a RuntimeEvent, never via legacy file logging."""
    import keeprollming.app as app_module
    import keeprollming.config as config_module

    dispatcher = EventDispatcher()
    received: list[RuntimeEvent] = []
    dispatcher.subscribe("execution.app", received.append)
    monkeypatch.setattr(app_module, "_event_dispatcher", dispatcher)
    monkeypatch.setattr(config_module, "check_config_reload", lambda: True)
    monkeypatch.setattr(config_module, "get_config_mtime", lambda: 123.0)

    async def cancel_after_first_poll(_: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(app_module.asyncio, "sleep", cancel_after_first_poll)

    async def run_watcher_once() -> None:
        with pytest.raises(asyncio.CancelledError):
            await app_module._config_watcher()

    asyncio.run(run_watcher_once())

    assert len(received) == 1
    event = received[0]
    assert event.type == "execution.app.config_reloaded"
    assert event.data == {
        "message": "Configuration reloaded",
        "config_mtime": 123.0,
    }
