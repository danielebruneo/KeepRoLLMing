"""Server event logging: setup, exception handling, body logging, error categorization."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import traceback
from typing import Any


# ── Server logger globals ──────────────────────────────────────────

_SERVER_LOGGER: logging.Logger | None = None
_LOG_FILE_PATH = os.getenv("LOG_FILE", "keeprollming.log")
_LOG_LEVEL = os.getenv("SERVER_LOG_LEVEL", "INFO").upper()

_DEBUG_LOGGER: logging.Logger | None = None
_DEBUG_LOG_FILE_PATH = os.getenv("DEBUG_LOG_FILE", "keeprollming.debug.log")
_DEBUG_LOG_LEVEL = os.getenv("DEBUG_LOG_LEVEL", "DEBUG").upper()


def setup_server_logging() -> logging.Logger:
    """Initialize server file logging. Returns the configured logger instance."""
    global _SERVER_LOGGER

    if _SERVER_LOGGER is not None:
        return _SERVER_LOGGER  # Already initialized

    _SERVER_LOGGER = logging.getLogger("keeprollming.server")
    _SERVER_LOGGER.setLevel(getattr(logging, _LOG_LEVEL, "INFO"))

    if _SERVER_LOGGER.handlers:
        return _SERVER_LOGGER

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            _LOG_FILE_PATH, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(getattr(logging, _LOG_LEVEL, "INFO"))
    except Exception as e:
        logger.warning("Could not create log file %s: %s", _LOG_FILE_PATH, e)
        return _SERVER_LOGGER

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    _SERVER_LOGGER.addHandler(file_handler)
    _SERVER_LOGGER.addHandler(console_handler)

    _SERVER_LOGGER.info(f"Server logging initialized: {_LOG_FILE_PATH} (level={_LOG_LEVEL})")
    return _SERVER_LOGGER


def get_server_logger() -> logging.Logger | None:
    """Get the server logger instance, initializing it if needed."""
    global _SERVER_LOGGER
    if _SERVER_LOGGER is None:
        return setup_server_logging()
    return _SERVER_LOGGER


def setup_debug_logging() -> logging.Logger:
    """Initialize debug file logging. Returns the configured logger instance."""
    global _DEBUG_LOGGER

    if _DEBUG_LOGGER is not None:
        return _DEBUG_LOGGER  # Already initialized

    _DEBUG_LOGGER = logging.getLogger("keeprollming.debug")
    _DEBUG_LOGGER.setLevel(getattr(logging, _DEBUG_LOG_LEVEL, "DEBUG"))

    if _DEBUG_LOGGER.handlers:
        return _DEBUG_LOGGER

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            _DEBUG_LOG_FILE_PATH, maxBytes=20 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
    except Exception as e:
        logger.warning("Could not create debug log file %s: %s", _DEBUG_LOG_FILE_PATH, e)
        return _DEBUG_LOGGER

    class DebugFormatter(logging.Formatter):
        def format(self, record):
            if not hasattr(record, 'req_id'):
                record.req_id = '-'
            return super().format(record)

    formatter = DebugFormatter(
        "%(asctime)s | %(levelname)-8s | REQ=%(req_id)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)

    _DEBUG_LOGGER.addHandler(file_handler)
    _DEBUG_LOGGER.info(f"Debug logging initialized: {_DEBUG_LOG_FILE_PATH} (level={_DEBUG_LOG_LEVEL})")
    return _DEBUG_LOGGER


def get_debug_logger() -> logging.Logger | None:
    """Get the debug logger instance, initializing it if needed."""
    global _DEBUG_LOGGER
    if _DEBUG_LOGGER is None:
        return setup_debug_logging()
    return _DEBUG_LOGGER


# ── Server event loggers ───────────────────────────────────────────

def log_server_event(level: str, message: str, **kwargs) -> None:
    """Log a server event to the file logger."""
    logger = get_server_logger()
    if logger:
        extra_msg = " | ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        full_message = f"{message} {extra_msg}".strip()
        getattr(logger, level.lower(), "info")(full_message)


def log_exception(exc: Exception, req_id: str, route: str = "-", model: str = "-", **extra) -> None:
    """Log an exception with full stacktrace to debug.log."""
    logger = get_debug_logger()
    if not logger:
        return

    context_parts = []
    if route and route != "-":
        context_parts.append(f"route={route}")
    if model and model != "-":
        context_parts.append(f"model={model}")
    for k, v in extra.items():
        context_parts.append(f"{k}={v}")
    context = " | ".join(context_parts) if context_parts else ""

    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_str = "".join(tb_lines)

    logger.error(
        f"EXCEPTION | {type(exc).__name__}: {str(exc)[:500]}\n{tb_str}",
        extra={"req_id": req_id}
    )


def log_body(kind: str, body: Any, req_id: str, route: str = "-", model: str = "-",
             indent: int = 1, max_chars: int = 50000) -> None:
    """Log a request/response body to debug.log with indentation."""
    logger = get_debug_logger()
    if not logger:
        return

    context = f"route={route} model={model}" if route and model else ""

    try:
        if isinstance(body, (dict, list)):
            body_str = json.dumps(body, ensure_ascii=False, indent=2)
        else:
            body_str = str(body)
    except Exception:
        body_str = f"<unserializable: {type(body).__name__}>"

    if len(body_str) > max_chars:
        body_str = body_str[:max_chars] + f"\n... <truncated {len(body_str) - max_chars} chars>"

    prefix = "  " * indent
    indented_body = "\n".join(f"{prefix}  {line}" for line in body_str.split("\n"))

    logger.debug(
        f"{kind.upper()} | {context}\n{indented_body}",
        extra={"req_id": req_id}
    )


def log_config_reload(old_mtime: float, new_mtime: float) -> None:
    """Log a config reload event."""
    log_server_event("INFO", "Config reloaded", old_mtime=old_mtime, new_mtime=new_mtime)


def log_config_error(error: str) -> None:
    """Log a config error during reload."""
    log_server_event("ERROR", f"Config reload failed: {error}")


# ── HTTPX error categorization ─────────────────────────────────────

def categorize_httpx_error(e: Exception) -> tuple[str, str]:
    """Categorize an httpx exception and return (error_type, error_message)."""
    import httpx

    err_type = "unknown"
    err_msg = str(e)[:500]

    if isinstance(e, httpx.ConnectError):
        err_type = "connection_failed"
        err_msg = _extract_connection_target(str(e)) or f"Connection failed: {str(e)[:200]}"
    elif isinstance(e, httpx.ConnectTimeout):
        err_type = "connection_timeout"
        err_msg = _extract_connection_target(str(e)) or "Connection timeout"
    elif isinstance(e, httpx.TimeoutException):
        err_type = "timeout"
        err_msg = str(e)[:200]
    elif isinstance(e, httpx.NetworkError):
        err_type = "network_error"
        err_msg = str(e)[:200]
    elif isinstance(e, httpx.HTTPStatusError):
        err_type = "http_status_error"
        status = getattr(e, 'response', None)
        if status:
            err_msg = f"HTTP {status.status_code}: {str(e)[:150]}"

    return err_type, err_msg


def _extract_connection_target(error_str: str) -> str | None:
    """Extract target URL/host from connection error string."""
    url_match = re.search(r'([a-zA-Z]+://[^\s\)]+)', error_str)
    if url_match:
        return url_match.group(1)[:200]

    host_match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)', error_str)
    if host_match:
        return host_match.group(1)

    return None


def log_request_error(req_id: str, error_type: str, endpoint: str | None = None,
                      model: str | None = None, upstream_url: str | None = None,
                      status_code: int | None = None, elapsed_ms: float | None = None,
                      **extra_fields) -> None:
    """Centralized error logging function for request errors."""
    from ..logger import log  # avoid circular at module level

    log(
        "ERROR", "request_error", req_id=req_id, error_type=error_type, endpoint=endpoint,
        model=model, upstream_url=upstream_url, status=status_code, elapsed_ms=elapsed_ms,
        **extra_fields
    )


def log_connection_error(req_id: str, error_type: str, upstream_url: str,
                         model: str | None = None, elapsed_ms: float | None = None,
                         **extra_fields) -> None:
    """Centralized connection error logging function."""
    from ..logger import log

    log(
        "ERROR", "connection_error", req_id=req_id, error_type=error_type,
        upstream_url=upstream_url, model=model, elapsed_ms=elapsed_ms, **extra_fields
    )


def log_fallback_error(req_id: str, from_model: str, to_model: str,
                       error_type: str, err_msg: str, **extra_fields) -> None:
    """Centralized fallback chain error logging."""
    from ..logger import log

    log(
        "WARN", "fallback_error", req_id=req_id, from_model=from_model, to_model=to_model,
        error_type=error_type, err_msg=err_msg[:500], **extra_fields
    )
