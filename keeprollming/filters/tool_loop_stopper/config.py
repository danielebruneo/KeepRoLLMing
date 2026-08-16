"""Typed settings for tool-loop request and stream hooks."""

from keeprollming.filters.contracts import FilterSettingsSchema

SCHEMA = FilterSettingsSchema({
    "max_attempts": int, "max_repeats": int, "max_retries": int,
    "tls_message": str, "nudge_message": str, "fallback_message": str,
    "fallback_streaming_message": str, "fallback_template": str,
    "trigger_patterns": list, "fuzzy_max_repeats": int,
    "fuzzy_look_back": int, "fuzzy_threshold": (int, float),
    "send_user_message": bool, "ab_loop_detection": bool,
    "upstream_url": str, "api_key": str,
})
