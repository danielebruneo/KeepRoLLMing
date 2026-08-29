"""Configuration-driven default observability projectors."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .formatters import CompactFormatter, JsonFormatter, PlainTextFormatter
from .projectors import Projector, QueuedProjector, RotatingFileSink, StdoutSink

DEFAULT_OBSERVABILITY_CONFIG = {
    "json": {
        "enabled": True,
        "level": "INFO",
        "path": "keeprollming.log.json",
        "max_bytes": 100 * 1024 * 1024,
        "backup_count": 5,
    },
    "plain": {
        "enabled": True,
        "level": "BASIC",
        "path": "keeprollming.log",
        "stdout": True,
        "max_bytes": 50 * 1024 * 1024,
        "backup_count": 5,
    },
    "server": {
        "enabled": True,
        "level": "INFO",
        "path": "server.log",
        "max_bytes": 50 * 1024 * 1024,
        "backup_count": 5,
    },
}


def _settings(config: dict | None, name: str) -> dict:
    result = dict(DEFAULT_OBSERVABILITY_CONFIG[name])
    if isinstance(config, dict) and isinstance(config.get(name), dict):
        result.update(config[name])
    return result


def _file_sink(log_dir: str, settings: dict) -> RotatingFileSink:
    return RotatingFileSink(
        str(Path(log_dir) / str(settings["path"])),
        max_bytes=int(settings["max_bytes"]),
        backup_count=int(settings["backup_count"]),
    )


def create_default_projectors(log_dir: str = ".", config: dict | None = None) -> List[Projector]:
    """Create bounded JSON, PLAIN and server projections from configuration."""
    json_settings = _settings(config, "json")
    plain_settings = _settings(config, "plain")
    server_settings = _settings(config, "server")
    projectors: List[Projector] = []

    if json_settings["enabled"]:
        projectors.append(Projector(
            "structured", level=str(json_settings["level"]), formatter=JsonFormatter(),
            sinks=[_file_sink(log_dir, json_settings)],
        ))
    if plain_settings["enabled"]:
        sinks = [_file_sink(log_dir, plain_settings)]
        if plain_settings["stdout"]:
            sinks.insert(0, StdoutSink())
        projectors.append(Projector(
            "main", level=str(plain_settings["level"]),
            formatter=PlainTextFormatter(), sinks=sinks,
        ))
    if server_settings["enabled"]:
        projectors.append(Projector(
            "server", selector="execution.performance.request_complete",
            level=str(server_settings["level"]), formatter=CompactFormatter(),
            sinks=[_file_sink(log_dir, server_settings)],
        ))
    return projectors


def activate_default_projectors(projectors: List[Projector], dispatcher) -> None:
    for projector in projectors:
        projector.activate(dispatcher)


def deactivate_default_projectors(projectors: List[Projector]) -> None:
    for projector in projectors:
        projector.deactivate()


async def start_queued_default_projectors(
    projectors: List[Projector],
    dispatcher,
    *,
    max_queue_size: int = 2048,
) -> List[QueuedProjector]:
    """Start default PLAIN/JSON projections outside the request event loop."""
    queued = [
        QueuedProjector(projector, max_queue_size=max_queue_size)
        for projector in projectors
    ]
    for projector in queued:
        await projector.start(dispatcher)
    return queued


async def stop_queued_default_projectors(projectors: List[QueuedProjector]) -> None:
    """Stop queued projections while preserving bounded shutdown behavior."""
    for projector in projectors:
        await projector.stop()
