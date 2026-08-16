"""Typed settings for tool-rewrite request and stream hooks."""

from keeprollming.filters.contracts import FilterSettingsSchema

SCHEMA = FilterSettingsSchema({"supported_patterns": list})
