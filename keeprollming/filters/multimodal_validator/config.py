"""Typed settings and validation for multimodal request validation."""

from collections.abc import Mapping

from keeprollming.filters.contracts import FilterSettingsSchema

SCHEMA = FilterSettingsSchema(
    {
        "strip_orphaned_markers": bool,
        "marker_patterns": list,
        "log_level": str,
        "max_images": int,
        "max_images_policy": str,
        "max_images_replacement_text": str,
        "strip_all_images": bool,
        "strip_all_files": bool,
        "files_replacement_text": str,
        "strip_last_image_max_retries": int,
        "strip_last_image_on_error": bool,
    }
)


def validate_settings(settings: Mapping[str, object]) -> None:
    """Validate the configured image-retention policy."""
    policy = settings.get("max_images_policy", "strip_first")
    if policy not in {"strip_first", "strip_latest"}:
        raise ValueError("filter 'multimodal_validator.max_images_policy' must be 'strip_first' or 'strip_latest'")
