"""Community-facing filter module namespace.

Exports are lazy so stream modules can depend on a filter module without
importing the complete request-filter registry during package initialization.
"""

__all__ = [
    "FilterConfigurationError",
    "FilterModule",
    "built_in_filter_modules",
    "normalize_filters",
    "request_priorities",
    "stream_priorities",
    "validate_filter_module_settings",
]


def __getattr__(name: str):
    if name in {"FilterConfigurationError", "normalize_filters"}:
        from . import configuration

        return getattr(configuration, name)
    if name in {
        "FilterModule",
        "built_in_filter_modules",
        "request_priorities",
        "stream_priorities",
        "validate_filter_module_settings",
    }:
        from . import registry

        return getattr(registry, name)
    raise AttributeError(name)
