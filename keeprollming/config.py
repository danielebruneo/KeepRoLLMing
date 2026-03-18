from __future__ import annotations

import os
import json
import yaml
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any, List

# Import routing module for Route class and functions
from keeprollming.routing import (
    Route,
    BUILTIN_ROUTES,
    DEFAULT_FALLBACK_ROUTE,
    resolve_route as _resolve_route,
    resolve_fallback_chain as _resolve_fallback_chain,
    get_route_settings as _get_route_settings,
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
    routes_config = config.get("routes", [])

    for route_data in routes_config:
        try:
            route = Route(
                name=route_data.get("name", "unnamed"),
                pattern=route_data.get("pattern", ""),
                summary_enabled=route_data.get("summary_enabled", True),
                passthrough_enabled=route_data.get("passthrough_enabled", False),
                main_model=route_data.get("main_model"),
                summary_model=route_data.get("summary_model"),
                ctx_len=route_data.get("ctx_len", 8192),
                max_tokens=route_data.get("max_tokens", 4096),
                transform_reasoning_content=route_data.get("transform_reasoning_content", False),
                add_empty_content_when_reasoning_only=route_data.get("add_empty_content_when_reasoning_only", False),
                reasoning_placeholder_content=route_data.get("reasoning_placeholder_content", ""),
                backend_model_pattern=route_data.get("backend_model_pattern"),
                fallback_chain=route_data.get("fallback_chain", []),
                circuit_breaker_enabled=route_data.get("circuit_breaker_enabled", False),
                failure_threshold=route_data.get("failure_threshold", 3),
                recovery_timeout=route_data.get("recovery_timeout", 60),
                cost_priority=route_data.get("cost_priority", 999),
            )
            user_routes.append(route)
        except Exception as e:
            # Log error but continue with other routes
            print(f"Warning: Failed to parse route '{route_data.get('name', 'unknown')}': {e}")

    return user_routes


def load_config() -> Dict[str, Any]:
    """Load configuration from config.yaml or config.json file, with fallback to environment variables."""

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

    # Set defaults for missing values
    config.setdefault("profiles", {
        "quick": {"main_model": "qwen2.5-3b-instruct", "summary_model": "qwen2.5-1.5b-instruct"},
        "main": {"main_model": "qwen2.5-v1-7b-instruct", "summary_model": "qwen2.5-3b-instruct"},
        "deep": {"main_model": "qwen2.5-27b-instruct", "summary_model": "qwen2.5-7b-instruct"}
    })

    # Add reasoning_content transformation option per profile (default: False)
    for profile_name in config.get("profiles", {}):
        if "transform_reasoning_content" not in config["profiles"][profile_name]:
            config["profiles"][profile_name]["transform_reasoning_content"] = False
        # Alternative: add empty content field when only reasoning is present (default: False)
        if "add_empty_content_when_reasoning_only" not in config["profiles"][profile_name]:
            config["profiles"][profile_name]["add_empty_content_when_reasoning_only"] = False
        # Content to use when adding empty content for reasoning-only responses (default: "")
        if "reasoning_placeholder_content" not in config["profiles"][profile_name]:
            config["profiles"][profile_name]["reasoning_placeholder_content"] = ""

    config.setdefault("model_aliases", {
        "local/quick": "quick",
        "quick": "quick",
        "local/main": "main",
        "main": "main",
        "local/deep": "deep",
        "deep": "deep"
    })

    config.setdefault("passthrough_prefix", "pass/")
    config.setdefault("default_profile", "main")
    config.setdefault("upstream_base_url", "http://127.0.0.1:1234/v1")
    config.setdefault("main_model", "qwen2.5-3b-instruct")
    config.setdefault("summary_model", "qwen2.5-1.5b-instruct")

    # Override with environment variables if they exist
    config["upstream_base_url"] = os.getenv("UPSTREAM_BASE_URL", config["upstream_base_url"]).rstrip("/")
    config["main_model"] = os.getenv("MAIN_MODEL", config["main_model"])
    config["summary_model"] = os.getenv("SUMMARY_MODEL", config["summary_model"])

    # Profile defaults (requested)
    config["quick_main_model"] = os.getenv("QUICK_MAIN_MODEL", config["profiles"]["quick"]["main_model"])
    config["quick_summary_model"] = os.getenv("QUICK_SUMMARY_MODEL", config["profiles"]["quick"]["summary_model"])
    config["base_main_model"] = os.getenv("BASE_MAIN_MODEL", config["profiles"]["main"]["main_model"])
    config["base_summary_model"] = os.getenv("BASE_SUMMARY_MODEL", config["profiles"]["main"]["summary_model"])
    config["deep_main_model"] = os.getenv("DEEP_MAIN_MODEL", config["profiles"]["deep"]["main_model"])
    config["deep_summary_model"] = os.getenv("DEEP_SUMMARY_MODEL", config["profiles"]["deep"]["summary_model"])

    # Other configuration values
    config["default_ctx_len"] = int(os.getenv("DEFAULT_CTX_LEN", str(config.get("default_ctx_len", "4096"))))
    config["summary_max_tokens"] = int(os.getenv("SUMMARY_MAX_TOKENS", str(config.get("summary_max_tokens", "256"))))
    config["safety_margin_tok"] = int(os.getenv("SAFETY_MARGIN_TOK", str(config.get("safety_margin_tok", "128"))))
    config["default_max_completion_tokens"] = int(os.getenv("DEFAULT_MAX_COMPLETION_TOKENS", str(config.get("default_max_completion_tokens", "900"))))

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

# Parse user-defined routes from config
USER_ROUTES: List[Route] = load_user_routes(CONFIG)


def resolve_route(client_model: str) -> Tuple[Optional[Route], str]:
    """
    Resolve a client-facing model name to the appropriate route and backend model.

    This function implements first-match-wins routing with fallback chain support.

    Args:
        client_model: The model name from the client request (e.g., "local/quick", "pass/openai/gpt-4")

    Returns:
        Tuple of (matched_route, backend_model_name)
        - route can be None if no match found (shouldn't happen with fallback)
        - backend_model is the actual model to use for routing
    """
    return _resolve_route(client_model, USER_ROUTES)


def resolve_fallback_chain(
    primary_route: Route,
    primary_backend: str,
    client_request_id: Optional[str] = None
) -> List[Tuple[Route, str]]:
    """
    Resolve a fallback chain for automatic rerouting when backend is unavailable.

    This function returns the complete list of (route, backend_model) pairs to try,
    starting with the primary route and following the fallback chain.

    Args:
        primary_route: The originally matched route
        primary_backend: The primary backend model name
        client_request_id: Optional request ID for tracking/debugging

    Returns:
        List of (route, backend_model) tuples in order to try
        Each tuple represents a routing attempt
    """
    return _resolve_fallback_chain(primary_route, primary_backend, client_request_id)


def get_route_settings(route: Route, backend_model: str) -> Dict[str, Any]:
    """
    Extract all settings from a matched route for use in the application.

    Args:
        route: The matched route
        backend_model: The resolved backend model name

    Returns:
        Dictionary of all route settings
    """
    return _get_route_settings(route, backend_model)


# Extract values from config
UPSTREAM_BASE_URL = CONFIG["upstream_base_url"]
MAIN_MODEL = CONFIG["main_model"]
SUMMARY_MODEL = CONFIG["summary_model"]

# Profile defaults (requested) - kept for backward compatibility
QUICK_MAIN_MODEL = CONFIG["quick_main_model"]
QUICK_SUMMARY_MODEL = CONFIG["quick_summary_model"]
BASE_MAIN_MODEL = CONFIG["base_main_model"]
BASE_SUMMARY_MODEL = CONFIG["base_summary_model"]
DEEP_MAIN_MODEL = CONFIG["deep_main_model"]
DEEP_SUMMARY_MODEL = CONFIG["deep_summary_model"]

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

PASSTHROUGH_PREFIX = CONFIG["passthrough_prefix"]


@dataclass(frozen=True)
class Profile:
    name: str
    main_model: str
    summary_model: str
    transform_reasoning_content: bool = False
    add_empty_content_when_reasoning_only: bool = False
    reasoning_placeholder_content: str = ""


def create_profiles_from_config(config: Dict[str, Any]) -> Dict[str, Profile]:
    """Create profile dictionary from configuration."""
    profiles = {}
    for name, profile_data in config["profiles"].items():
        profiles[name] = Profile(
            name,
            profile_data["main_model"],
            profile_data["summary_model"],
            transform_reasoning_content=profile_data.get("transform_reasoning_content", False),
            add_empty_content_when_reasoning_only=profile_data.get("add_empty_content_when_reasoning_only", False),
            reasoning_placeholder_content=profile_data.get("reasoning_placeholder_content", "")
        )
    return profiles


PROFILES: Dict[str, Profile] = create_profiles_from_config(CONFIG)

# Client-facing model aliases (LibreChat or your own) - kept for backward compatibility
MODEL_ALIASES: Dict[str, str] = CONFIG["model_aliases"]


def resolve_profile_and_models(client_model: str) -> Tuple[Optional[Profile], str, str, bool, bool, bool, str]:
    """Return (profile_or_none, upstream_main_model, summary_model, passthrough_enabled, transform_reasoning_content, add_empty_content_when_reasoning_only, reasoning_placeholder_content)."""
    if isinstance(client_model, str) and client_model.startswith(PASSTHROUGH_PREFIX):
        backend = client_model[len(PASSTHROUGH_PREFIX):].strip()
        # If empty, fallback to MAIN_MODEL but keep passthrough disabled to avoid surprises
        if not backend:
            return (None, MAIN_MODEL, SUMMARY_MODEL, False, False, False, "")
        # For passthrough, check if the upstream model matches any profile and use that profile's setting
        transform_reasoning = False
        add_empty_content = False
        placeholder = ""
        for p in PROFILES.values():
            if backend.startswith(p.main_model):
                transform_reasoning = p.transform_reasoning_content
                add_empty_content = p.add_empty_content_when_reasoning_only
                placeholder = p.reasoning_placeholder_content
                break
        return (None, backend, SUMMARY_MODEL, True, transform_reasoning, add_empty_content, placeholder)

    key = MODEL_ALIASES.get(client_model)
    if key and key in PROFILES:
        p = PROFILES[key]
        return (p, p.main_model, p.summary_model, False, p.transform_reasoning_content, p.add_empty_content_when_reasoning_only, p.reasoning_placeholder_content)

    # No alias: treat it as an explicit upstream model name (backwards-compatible)
    return (None, client_model or MAIN_MODEL, SUMMARY_MODEL, False, False, False, "")
