"""Typed settings for timestamp request and stream hooks."""

from keeprollming.filters.contracts import FilterSettingsSchema

SCHEMA = FilterSettingsSchema({
    "template": str, "timezone": str, "tail_buffer_size": int,
    "always": bool, "format": str,
})
