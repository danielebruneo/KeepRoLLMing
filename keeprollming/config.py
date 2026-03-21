from __future__ import annotations

import os
import json
import yaml
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Any, List

# Import routing module for Route class and functions
from keeprollming.routing import (
    Route,
    BUILTIN_ROUTES,
    DEFAULT_FALLBACK_ROUTE,
    resolve_route as _resolve_route,
    resolve_fallback_chain as _resolve_fallback_chain,
    get_route_settings as _get_route_settings,
    DefaultSettings,
    ModelConfig,
    _UNSET,
)

# ----------------------------
# Configuration
# ----------------------------


def load_user_routes(config: Dict[str, Any]) -> List[Route]:
    """
    Parse user-defined routes from configuration.

    Args:
        config: Loaded configuration dictionary

    Returns:
        List of Route objects from user configuration
    """
    user_routes = []
    routes_config = config.get("routes", {})

    # Handle both list format and dict format
    if isinstance(routes_config, list):
        # List format: [{"name": "quick", ...}, {"name": "main", ...}]
        for route_data in routes_config:
            try:
                name = route_data.get("name", "unnamed")
                pattern = route_data.get("pattern", name)

                # Helper to get value or _UNSET if not specified, otherwise return default
                def get_or_unset(key, default=None):
                    if key in route_data:
                        return route_data[key]  # type: ignore
                    elif default is None and key != "fallback_chain":
                        return _UNSET  # type: ignore
                    else:
                        return default  # type: ignore

                route = Route(
                    name=name,
                    pattern=pattern,
                    summary_enabled=get_or_unset("summary_enabled", True),  # type: ignore
                    passthrough_enabled=get_or_unset("passthrough_enabled", False),  # type: ignore
                    model=get_or_unset("model"),  # type: ignore
                    summary_model=get_or_unset("summary_model"),  # type: ignore
                    ctx_len=get_or_unset("ctx_len", _UNSET),  # type: ignore
                    max_tokens=get_or_unset("max_tokens", _UNSET),  # type: ignore
                    transform_reasoning_content=get_or_unset("transform_reasoning_content", False),  # type: ignore
                    add_empty_content_when_reasoning_only=get_or_unset("add_empty_content_when_reasoning_only", False),  # type: ignore
                    reasoning_placeholder_content=get_or_unset("reasoning_placeholder_content", ""),  # type: ignore
                    model_pattern=get_or_unset("model_pattern"),  # type: ignore
                    upstream_url=get_or_unset("upstream_url", None),  # Use _UNSET to enable inheritance from parent routes
                    upstream_headers=get_or_unset("upstream_headers", {}),  # type: ignore
                    fallback_chain=get_or_unset("fallback_chain", []),  # type: ignore
                    circuit_breaker_enabled=get_or_unset("circuit_breaker_enabled", False),  # type: ignore
                    failure_threshold=get_or_unset("failure_threshold", 3),  # type: ignore
                    recovery_timeout=get_or_unset("recovery_timeout", 60),  # type: ignore
                    cost_priority=get_or_unset("cost_priority", 999),  # type: ignore
                    extends=route_data.get("extends"),
                )
                user_routes.append(route)
            except Exception as e:
                print(f"Warning: Failed to parse route: {e}")
    elif isinstance(routes_config, dict):
        # Dict format: {"quick": {...}, "main": {...}}

        # Parse all routes (is_private flag is now a property on each route)
        for name, route_data in routes_config.items():
            if not isinstance(route_data, dict):
                continue

            try:
                pattern = route_data.get("pattern", name)

                # Support both 'pattern' and 'patterns' (plural) fields
                patterns = route_data.get("patterns")
                if patterns:
                    if isinstance(patterns, list):
                        pattern = "|".join(patterns)
                    else:
                        pattern = str(patterns)

                # Helper to get value or _UNSET if not specified, otherwise return default
                def get_or_unset(key, default=None):
                    if key in route_data:
                        return route_data[key]  # type: ignore
                    elif default is None and key != "fallback_chain":
                        return _UNSET  # type: ignore
                    else:
                        return default  # type: ignore

                # Handle multiple extends as list or single value
                extends = route_data.get("extends")
                if isinstance(extends, list):
                    extends = ",".join(extends)

                # Extract is_private flag from config (default False for non-private routes)
                is_private = route_data.get("is_private", False)

                # Support both 'model' and 'main_model' (backward compatibility)
                model = get_or_unset("model")
                if model is _UNSET:
                    model = get_or_unset("main_model")

                route = Route(
                    name=name,
                    pattern=pattern,
                    summary_enabled=get_or_unset("summary_enabled", True),  # type: ignore
                    passthrough_enabled=get_or_unset("passthrough_enabled", False),  # type: ignore
                    model=model,  # type: ignore
                    summary_model=get_or_unset("summary_model"),  # type: ignore
                    ctx_len=get_or_unset("ctx_len", _UNSET),  # type: ignore
                    max_tokens=get_or_unset("max_tokens", _UNSET),  # type: ignore
                    transform_reasoning_content=get_or_unset("transform_reasoning_content", False),  # type: ignore
                    add_empty_content_when_reasoning_only=get_or_unset("add_empty_content_when_reasoning_only", False),  # type: ignore
                    reasoning_placeholder_content=get_or_unset("reasoning_placeholder_content", ""),  # type: ignore
                    model_pattern=get_or_unset("model_pattern"),  # type: ignore
                    upstream_url=get_or_unset("upstream_url", None),  # Use _UNSET to enable inheritance from parent routes
                    upstream_headers=get_or_unset("upstream_headers", {}),  # type: ignore
                    fallback_chain=get_or_unset("fallback_chain", []),  # type: ignore
                    circuit_breaker_enabled=get_or_unset("circuit_breaker_enabled", False),  # type: ignore
                    failure_threshold=get_or_unset("failure_threshold", 3),  # type: ignore
                    recovery_timeout=get_or_unset("recovery_timeout", 60),  # type: ignore
                    cost_priority=get_or_unset("cost_priority", 999),  # type: ignore
                    extends=extends,
                    _is_private=is_private,
                )
                user_routes.append(route)
            except Exception as e:
                print(f"Warning: Failed to parse route '{name}': {e}")

    return user_routes


def resolve_route_settings(
    route: Route,
    models_config: Dict[str, ModelConfig],
    defaults: DefaultSettings,
) -> Tuple[int, int]:
    """
    Resolve ctx_len and max_tokens for a route by applying 3-level hierarchy.

    Priority order (highest to lowest):
      1. Route level settings
      2. Model-specific settings
      3. Root-level defaults

    Args:
        route: The matched route with potentially unset ctx_len/max_tokens
        models_config: Dictionary mapping model names to their configs
        defaults: Global default settings

    Returns:
        Tuple of (resolved_ctx_len, resolved_max_tokens)
    """
    # Get main model name for lookup
    model = route.model

    # Start with global defaults
    ctx_len = defaults.ctx_len
    max_tokens = defaults.max_tokens

    # Apply model-specific settings if available
    if model and model in models_config:
        model_cfg = models_config[model]

        # Override with model values if set (not _UNSET)
        if model_cfg.ctx_len is not _UNSET:  # type: ignore
            ctx_len = model_cfg.ctx_len  # type: ignore

        if model_cfg.max_tokens is not _UNSET:  # type: ignore
            max_tokens = model_cfg.max_tokens  # type: ignore

    # Apply route-level overrides (highest priority)
    if route.ctx_len is not _UNSET:  # type: ignore
        ctx_len = route.ctx_len  # type: ignore

    if route.max_tokens is not _UNSET:  # type: ignore
        max_tokens = route.max_tokens  # type: ignore

    return ctx_len, max_tokens


def load_config() -> Dict[str, Any]:
    """Load configuration from config.yaml or config.json file."""

    # Check for custom config file via environment variable
    custom_config_path = os.getenv("CONFIG_FILE")

    if custom_config_path and os.path.exists(custom_config_path):
        # Load from custom config path
        if custom_config_path.endswith(".yaml") or custom_config_path.endswith(".yml"):
            with open(custom_config_path, "r") as f:
                config = yaml.safe_load(f)
        else:
            with open(custom_config_path, "r") as f:
                config = json.load(f)
    else:
        # Try to load from config.yaml first
        try:
            with open("config.yaml", "r") as f:
                config = yaml.safe_load(f)
        except FileNotFoundError:
            # Try to load from config.json if yaml doesn't exist
            try:
                with open("config.json", "r") as f:
                    config = json.load(f)
            except FileNotFoundError:
                # If no config file exists, use defaults
                config = {}

    # Set defaults for missing values (flat structure now)
    config.setdefault("upstream_base_url", "http://127.0.0.1:1234/v1")

    # Allow UPSTREAM_BASE_URL env var to override the config file value
    if "UPSTREAM_BASE_URL" in os.environ:
        config["upstream_base_url"] = os.environ["UPSTREAM_BASE_URL"]

    # Parse flat default settings at root level
    config["defaults"] = {
        "ctx_len": int(config.get("ctx_len", 8192)),
        "max_tokens": int(config.get("max_tokens", 4096)),
        "summary_enabled": config.get("summary_enabled", True),
        "passthrough_enabled": config.get("passthrough_enabled", False),
    }

    # Other configuration values (no environment variable overrides - all in YAML now)
    config["default_ctx_len"] = int(os.getenv("DEFAULT_CTX_LEN", str(config["defaults"]["ctx_len"])))
    config["summary_max_tokens"] = int(os.getenv("SUMMARY_MAX_TOKENS", str(config.get("summary_max_tokens", "512"))))
    config["safety_margin_tok"] = int(os.getenv("SAFETY_MARGIN_TOK", str(config.get("safety_margin_tok", "128"))))
    
    # max_completion_tokens: if not set, use _UNSET to indicate "don't send upstream"
    default_max_completion_tokens = config.get("default_max_completion_tokens")
    if default_max_completion_tokens is None:
        from .routing import _UNSET  # type: ignore
        config["default_max_completion_tokens"] = _UNSET
    else:
        config["default_max_completion_tokens"] = int(default_max_completion_tokens)

    config["summary_mode"] = os.getenv("SUMMARY_MODE", config.get("summary_mode", "cache_append")).strip().lower()
    config["summary_cache_enabled"] = os.getenv("SUMMARY_CACHE_ENABLED", str(config.get("summary_cache_enabled", "1"))).strip().lower() not in {"0", "false", "no", "off"}
    config["summary_cache_dir"] = os.getenv("SUMMARY_CACHE_DIR", config.get("summary_cache_dir", "./__summary_cache"))
    config["summary_cache_fingerprint_msgs"] = int(os.getenv("SUMMARY_CACHE_FINGERPRINT_MSGS", str(config.get("summary_cache_fingerprint_msgs", "1"))))
    config["summary_min_raw_tail"] = max(1, int(os.getenv("SUMMARY_MIN_RAW_TAIL", str(config.get("summary_min_raw_tail", "1")))))
    config["summary_force_consolidate"] = int(os.getenv("SUMMARY_FORCE_CONSOLIDATE", str(config.get("summary_force_consolidate", "0"))))
    config["summary_consolidate_when_needed"] = int(os.getenv("SUMMARY_CONSOLIDATE_WHEN_NEEDED", str(config.get("summary_consolidate_when_needed", "1"))))

    # Max chars for logging large payloads (input conversation, summary requests, etc.)
    config["log_payload_max_chars"] = int(os.getenv("LOG_PAYLOAD_MAX_CHARS", str(config.get("log_payload_max_chars", "20000000"))))

    return config


# Load configuration
CONFIG = load_config()

# Track config file modification time for hot reload
_CONFIG_FILE_PATH: str | None = None
try:
    _config_path = os.getenv("CONFIG_FILE", "config.yaml")
    if os.path.exists(_config_path):
        _CONFIG_FILE_PATH = _config_path
except Exception:
    pass

# Store initial modification time
_CONFIG_LAST_MTIME: float = 0.0
if _CONFIG_FILE_PATH and os.path.exists(_CONFIG_FILE_PATH):
    try:
        _CONFIG_LAST_MTIME = os.path.getmtime(_CONFIG_FILE_PATH)
    except Exception:
        pass


def get_config_mtime() -> float | None:
    """Get the current modification time of the config file."""
    if _CONFIG_FILE_PATH and os.path.exists(_CONFIG_FILE_PATH):
        try:
            return os.path.getmtime(_CONFIG_FILE_PATH)
        except Exception:
            return None
    return None


def check_config_reload() -> bool:
    """
    Check if the config file has been modified and needs reloading.
    
    Returns:
        True if config was reloaded, False otherwise or on error
    """
    global CONFIG, USER_ROUTES, DEFAULTS, _CONFIG_LAST_MTIME
    
    current_mtime = get_config_mtime()
    if current_mtime is None:
        return False
    
    # Check if file has been modified
    if current_mtime > _CONFIG_LAST_MTIME:
        try:
            # Reload configuration
            new_config = load_config()
            
            # Validate before applying (basic check)
            if "routes" not in new_config and "upstream_base_url" not in new_config:
                return False
            
            # Update global state atomically
            CONFIG.clear()
            CONFIG.update(new_config)
            USER_ROUTES.clear()
            USER_ROUTES.extend(load_user_routes(CONFIG))
            
            # Update DEFAULTS
            ctx_len = CONFIG["defaults"]["ctx_len"] if "defaults" in CONFIG else 8192
            max_tokens = CONFIG["defaults"]["max_tokens"] if "defaults" in CONFIG else 4096
            summary_enabled = CONFIG["defaults"]["summary_enabled"] if "defaults" in CONFIG else True
            
            DEFAULTS = DefaultSettings(ctx_len=ctx_len, max_tokens=max_tokens, summary_enabled=summary_enabled)
            
            # Update cached mtime
            _CONFIG_LAST_MTIME = current_mtime
            
            return True
        except Exception as e:
            print(f"Warning: Config reload failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    return False


# Parse user-defined routes from config
USER_ROUTES: List[Route] = load_user_routes(CONFIG)

# Create DefaultSettings object for resolution
DEFAULTS = DefaultSettings(
    ctx_len=CONFIG["defaults"]["ctx_len"],
    max_tokens=CONFIG["defaults"]["max_tokens"],
    summary_enabled=CONFIG["defaults"]["summary_enabled"],
)

# Models config removed - now defined inline in routes


def resolve_route(client_model: str) -> Tuple[Optional[Route], str]:
    """
    Resolve a client-facing model name to the appropriate route and backend model.

    This function implements first-match-wins routing with fallback chain support.

    Args:
        client_model: The model name from the client request (e.g., "local/quick", "pass/openai/gpt-4")

    Returns:
        Tuple of (matched_route, model_name)
        - route can be None if no match found (shouldn't happen with fallback)
        - model is the actual model to use for routing
    """
    return _resolve_route(client_model, USER_ROUTES)


def resolve_fallback_chain(
    primary_route: Route,
    primary_backend: str,
    client_request_id: Optional[str] = None
) -> List[Tuple[Route, str]]:
    """
    Resolve a fallback chain for automatic rerouting when backend is unavailable.

    This function returns the complete list of (route, model) pairs to try,
    starting with the primary route and following the fallback chain.

    Args:
        primary_route: The originally matched route
        primary_backend: The primary backend model name
        client_request_id: Optional request ID for tracking/debugging

    Returns:
        List of (route, model) tuples in order to try
        Each tuple represents a routing attempt
    """
    return _resolve_fallback_chain(primary_route, primary_backend, client_request_id)


def get_route_settings(route: Route, model: str) -> Dict[str, Any]:
    """
    Extract all settings from a matched route for use in the application.

    Args:
        route: The matched route
        model: The resolved backend model name

    Returns:
        Dictionary of all route settings
    """
    return _get_route_settings(route, model)


# Extract values from config (no environment variable overrides - all in YAML now)
UPSTREAM_BASE_URL = CONFIG["upstream_base_url"]
DEFAULT_CTX_LEN = CONFIG["default_ctx_len"]
SUMMARY_MAX_TOKENS = CONFIG["summary_max_tokens"]
SAFETY_MARGIN_TOK = CONFIG["safety_margin_tok"]
DEFAULT_MAX_COMPLETION_TOKENS = CONFIG["default_max_completion_tokens"]

SUMMARY_MODE = CONFIG["summary_mode"]
SUMMARY_CACHE_ENABLED = CONFIG["summary_cache_enabled"]
SUMMARY_CACHE_DIR = CONFIG["summary_cache_dir"]
SUMMARY_CACHE_FINGERPRINT_MSGS = CONFIG["summary_cache_fingerprint_msgs"]
SUMMARY_MIN_RAW_TAIL = CONFIG["summary_min_raw_tail"]
SUMMARY_FORCE_CONSOLIDATE = CONFIG["summary_force_consolidate"]
SUMMARY_CONSOLIDATE_WHEN_NEEDED = CONFIG["summary_consolidate_when_needed"]

# Max chars for logging large payloads (input conversation, summary requests, etc.)
LOG_PAYLOAD_MAX_CHARS = CONFIG["log_payload_max_chars"]


@dataclass(frozen=True)
class Profile:
    """Legacy profile class - kept for backward compatibility during transition."""
    name: str
    model: str
    summary_model: str
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""


def create_profiles_from_config(config: Dict[str, Any]) -> Dict[str, Profile]:
    """Create profile dictionary from configuration (legacy support)."""
    profiles = {}
    for name, profile_data in config.get("profiles", {}).items():
        profiles[name] = Profile(
            name,
            profile_data["model"],
            profile_data["summary_model"],
            transform_reasoning_content=profile_data.get("transform_reasoning_content", False),
            add_empty_content_when_reasoning_only=profile_data.get("add_empty_content_when_reasoning_only", False),
            reasoning_placeholder_content=profile_data.get("reasoning_placeholder_content", "")
        )
    return profiles


PROFILES: Dict[str, Profile] = create_profiles_from_config(CONFIG)

# Client-facing model aliases (LibreChat or your own) - kept for backward compatibility
MODEL_ALIASES: Dict[str, str] = CONFIG.get("model_aliases", {})

PASSTHROUGH_PREFIX = CONFIG.get("passthrough_prefix", "pass/")


def get_private_routes() -> set:
    """Get the set of private route names (those marked with is_private=True)."""
    return {route.name for route in USER_ROUTES if route._is_private}


def resolve_profile_and_models(client_model: str) -> Tuple[Optional[Profile], str, str, bool, bool, bool, str]:
    """Legacy function - deprecated. Use resolve_route() instead."""
    # For backward compatibility, delegate to new routing system
    route, model = resolve_route(client_model)
    settings = get_route_settings(route, model)

    profile = None
    if MODEL_ALIASES.get(client_model) in PROFILES:
        profile = PROFILES[MODEL_ALIASES[client_model]]

    return (
        profile,
        settings["model"],
        settings["summary_model"] or settings["model"],
        settings["passthrough_enabled"],
        settings["transform_reasoning_content"],
        settings["add_empty_content_when_reasoning_only"],
        settings["reasoning_placeholder_content"]
    )
