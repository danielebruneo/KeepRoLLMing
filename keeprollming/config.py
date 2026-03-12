from __future__ import annotations

import os
import json
import yaml
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any

# ----------------------------
# Configuration
# ----------------------------

def load_config() -> Dict[str, Any]:
    """Load configuration from config.yaml or config.json file, with fallback to environment variables."""
    
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

# Extract values from config
UPSTREAM_BASE_URL = CONFIG["upstream_base_url"]
MAIN_MODEL = CONFIG["main_model"]
SUMMARY_MODEL = CONFIG["summary_model"]

# Profile defaults (requested)
QUICK_MAIN_MODEL = CONFIG["quick_main_model"]
QUICK_SUMMARY_MODEL = CONFIG["quick_summary_model"]
BASE_MAIN_MODEL = CONFIG["base_main_model"]
BASE_SUMMARY_MODEL = CONFIG["base_summary_model"]
DEEP_MAIN_MODEL = CONFIG["deep_main_model"]
DEEP_SUMMARY_MODEL = CONFIG["deep_summary_model"]

DEFAULT_CTX_LEN = CONFIG["default_ctx_len"]
SUMMARY_MAX_TOKENS = CONFIG["summary_max_tokens"]
SAFETY_MARGIN_TOK = CONFIG["safety_margin_tok"]

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


def create_profiles_from_config(config: Dict[str, Any]) -> Dict[str, Profile]:
    """Create profile dictionary from configuration."""
    profiles = {}
    for name, profile_data in config["profiles"].items():
        profiles[name] = Profile(name, profile_data["main_model"], profile_data["summary_model"])
    return profiles


PROFILES: Dict[str, Profile] = create_profiles_from_config(CONFIG)

# Client-facing model aliases (LibreChat or your own)
MODEL_ALIASES: Dict[str, str] = CONFIG["model_aliases"]


def resolve_profile_and_models(client_model: str) -> Tuple[Optional[Profile], str, str, bool]:
    """Return (profile_or_none, upstream_main_model, summary_model, passthrough_enabled)."""
    if isinstance(client_model, str) and client_model.startswith(PASSTHROUGH_PREFIX):
        backend = client_model[len(PASSTHROUGH_PREFIX):].strip()
        # If empty, fallback to MAIN_MODEL but keep passthrough disabled to avoid surprises
        if not backend:
            return (None, MAIN_MODEL, SUMMARY_MODEL, False)
        return (None, backend, SUMMARY_MODEL, True)

    key = MODEL_ALIASES.get(client_model)
    if key and key in PROFILES:
        p = PROFILES[key]
        return (p, p.main_model, p.summary_model, False)

    # No alias: treat it as an explicit upstream model name (backwards-compatible)
    return (None, client_model or MAIN_MODEL, SUMMARY_MODEL, False)
