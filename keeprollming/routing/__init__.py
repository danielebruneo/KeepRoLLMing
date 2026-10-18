"""Routing module — thin re-export layer for backward compatibility.

All routing logic has been moved to keeprollming/routing/router.py.
This module re-exports the public API for existing importers.

New code should import from keeprollming.routing directly.
"""

from ..types import ModelConfig  # noqa: F401
from .affinity import (  # noqa: F401
    AffinityLookup,
    SessionAffinityRegistry,
    get_session_affinity_registry,
)
from .balancing import (  # noqa: F401
    BalanceConfigurationError,
    BalanceSelection,
    NoEligibleBalanceTarget,
    parse_balance_targets,
    parse_capability_enforcement,
    parse_preserved_filter_names,
    parse_preserved_override_keys,
    resolve_balance_selection,
    validate_balance_policies,
)
from .plan import RoutePlan, UpstreamAttempt  # noqa: F401
from .requirements import RequestRequirements, derive_request_requirements  # noqa: F401
from .router import *  # noqa: F403
from .router import (  # noqa: F401
    DEFAULT_FALLBACK_ROUTE,
    DefaultSettings,
    RerouteResolution,
    Route,
    RouteMatch,
    RouteRerouteError,
    resolve_reroute_destination,
)
