"""Typed settings for multimodal request validation."""

from keeprollming.filters.contracts import FilterSettingsSchema

SCHEMA = FilterSettingsSchema({
    "strip_orphaned_markers": bool, "marker_patterns": list,
    "log_level": str, "max_images": int,
    "max_images_replacement_text": str, "strip_all_images": bool,
    "strip_last_image_max_retries": int,
    "strip_last_image_on_error": bool,
})
