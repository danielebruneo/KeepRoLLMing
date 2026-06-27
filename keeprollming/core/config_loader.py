"""Configuration loading and hot-reload management.

This module extracts configuration-related logic from app.py including:
- Config file watching and reloading
- Route settings extraction
- Default values and constants
"""

import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

from ..config import (
    SAFETY_MARGIN_TOK,
    SUMMARY_MODE,
    SUMMARY_CACHE_ENABLED,
    SUMMARY_CACHE_DIR,
    SUMMARY_CACHE_FINGERPRINT_MSGS,
    SUMMARY_FORCE_CONSOLIDATE,
    SUMMARY_CONSOLIDATE_WHEN_NEEDED,
    UPSTREAM_BASE_URL,
    DEFAULT_MAX_COMPLETION_TOKENS,
    resolve_route,
    resolve_fallback_chain,
    get_route_settings,
    CONFIG,
    DEFAULTS,
    resolve_route_settings,
)
from ..routing import Route


# Logging and payload constants
LOG_PAYLOAD_MAX_CHARS = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", "20000000"))
MAX_SSE_BYTES = 10_000_000  # Max SSE body capture for logging
LOG_STREAM_PROGRESS_INTERVAL_MS = max(0, int(os.getenv("LOG_STREAM_PROGRESS_INTERVAL_MS", "1000")))
ENABLE_OPENAI_STREAM_COMPAT = os.getenv("ENABLE_OPENAI_STREAM_COMPAT", "1") == "1"


class ConfigLoader:
    """Handles configuration loading and hot-reload functionality.
    
    This class provides a centralized interface for accessing configuration
    values and managing automatic reloads when config.yaml changes.
    """
    
    def __init__(self):
        self._last_config_mtime: float = 0.0
        self._watch_task: Optional[asyncio.Task] = None
    
    async def start_watcher(self) -> None:
        """Start the background config watcher task."""
        self._watch_task = asyncio.create_task(_config_watcher())
    
    async def stop_watcher(self) -> None:
        """Stop the background config watcher task."""
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
    
    def get_route_settings(
        self,
        route: Route,
        model: str
    ) -> "RouteSettings":
        """Get route settings for a specific model.

        Args:
            route: The resolved Route object
            model: The model name to get settings for

        Returns:
            RouteSettings object with all resolved settings
        """
        return get_route_settings(route, model)


async def _config_watcher() -> None:
    """Background task to watch for config file changes.
    
    Checks config.yaml every 2 seconds for modifications and triggers
    a reload when changes are detected.
    """
    from ..config import check_config_reload
    
    interval = 2.0  # Check every 2 seconds
    while True:
        await asyncio.sleep(interval)
        check_config_reload()


def create_route_settings(route: Route, model: str) -> Dict[str, Any]:
    """Extract and normalize route settings into a structured dictionary.

    Args:
        route: The resolved Route object
        model: The model name

    Returns:
        Dictionary containing all route-specific settings including:
        - upstream_model: The actual model to use for API calls
        - summary_model: Model to use for summarization
        - passthrough_enabled: Whether passthrough mode is enabled
        - summary_enabled: Whether summarization is enabled
        - transform_reasoning_content: Whether to transform reasoning_content
        - add_empty_content_when_reasoning_only: Inject placeholder if only reasoning
        - reasoning_placeholder_content: Content for placeholder injection
        - tool_rewrite_enabled: Whether tool call rewriting is enabled
    """
    rs = get_route_settings(route, model)

    return {
        "upstream_model": rs.upstream_model,
        "summary_model": rs.summary_model,
        "passthrough_enabled": rs.passthrough_enabled,
        "summary_enabled": rs.summary_enabled,
        "transform_reasoning_content": rs.transform_reasoning_content,
        "add_empty_content_when_reasoning_only": rs.add_empty_content_when_reasoning_only,
        "reasoning_placeholder_content": rs.reasoning_placeholder_content,
        "tool_rewrite_enabled": getattr(route, "tool_rewrite_enabled", False),
    }


def resolve_route_with_settings(
    client_model: str
) -> Tuple[Optional[Route], str, Dict[str, Any]]:
    """Resolve a client model request to a route with full settings.
    
    This is a convenience function that combines route resolution with
    settings extraction into a single call.
    
    Args:
        client_model: The model name specified by the client
        
    Returns:
        Tuple of (route, upstream_model, settings_dict)
    """
    route, model = resolve_route(client_model)
    settings = create_route_settings(route, model)
    return route, model, settings


def get_fallback_chain(
    route: Route, 
    model: str, 
    req_id: str
) -> List[Tuple[Optional[Route], str]]:
    """Get the fallback chain for a given route and model.
    
    Args:
        route: The primary route object
        model: The primary model name
        req_id: Request ID for logging purposes
        
    Returns:
        List of (route, model) tuples representing fallback options
    """
    return resolve_fallback_chain(route, model, req_id)
