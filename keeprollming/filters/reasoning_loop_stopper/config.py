"""Typed settings for reasoning-loop request and stream hooks."""

from keeprollming.filters.contracts import FilterSettingsSchema

SCHEMA = FilterSettingsSchema({
    "max_attempts": int, "max_repeats": int, "max_retries": int, "rls_message": str,
    "fallback_message": str, "fallback_streaming_message": str,
    "send_user_message": bool, "upstream_url": str, "api_key": str,
})
