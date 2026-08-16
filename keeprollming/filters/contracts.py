"""Small contracts shared by community filter modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class FilterSettingsSchema:
    """Typed settings accepted by a single operator-facing filter module."""

    fields: Mapping[str, type | tuple[type, ...]]
