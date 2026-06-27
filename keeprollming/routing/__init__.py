"""Routing module — thin re-export layer for backward compatibility.

All routing logic has been moved to keeprollming/routing/router.py.
This module re-exports the public API for existing importers.

New code should import from keeprollming.routing directly.
"""

from .router import *  # noqa: F403
from .router import Route, RouteMatch, DefaultSettings, ModelConfig, DEFAULT_FALLBACK_ROUTE  # noqa: F401
