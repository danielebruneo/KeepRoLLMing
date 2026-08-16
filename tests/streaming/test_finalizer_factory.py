"""Unit tests for the declarative V2 finalizer factory."""

from keeprollming.streaming.finalizers import ToolCallFinalizer
from keeprollming.filters.nudge.stream import NudgeContinuationFinalizer
from keeprollming.filters.reasoning_loop_stopper.stream import RLSFinalizer
from keeprollming.filters.tool_loop_stopper.stream import TLSFinalizer
from keeprollming.filters.timestamp.stream import TimestampFinalizer
from keeprollming.filters.tool_rewrite.stream import ToolRewriteFinalizer
from keeprollming.streaming.finalizer_factory import build_finalizers


def _by_type(finalizers, type_):
    return [finalizer for finalizer in finalizers if isinstance(finalizer, type_)]


def test_factory_always_includes_protocol_finalizer():
    finalizers = build_finalizers({})
    assert [type(finalizer) for finalizer in finalizers] == [ToolCallFinalizer]


def test_factory_uses_enabled_route_configuration_only():
    finalizers = build_finalizers({
        "tool_rewrite": {"enabled": True, "supported_patterns": ["nested"]},
        "timestamp": {"enabled": True, "timezone": "Europe/Rome"},
        "model_nudge": {"enabled": True, "nudge_message": "Continue now."},
        "model_tool_loop_stopper": {"enabled": False},
    })

    assert len(_by_type(finalizers, ToolRewriteFinalizer)) == 1
    assert _by_type(finalizers, ToolRewriteFinalizer)[0].supported_patterns == ["nested"]
    assert len(_by_type(finalizers, TimestampFinalizer)) == 1
    assert len(_by_type(finalizers, NudgeContinuationFinalizer)) == 1
    assert not _by_type(finalizers, TLSFinalizer)


def test_factory_orders_finalizers_by_protocol_priority():
    finalizers = build_finalizers({
        "tool_rewrite": {"enabled": True},
        "timestamp": {"enabled": True},
        "model_nudge": {"enabled": True},
        "model_tool_loop_stopper": {"enabled": True},
        "reasoning_loop_stopper": {"enabled": True},
    })
    assert [finalizer.priority for finalizer in finalizers] == [15, 20, 40, 50, 55, 60]


def test_factory_honors_explicit_route_priority_override():
    """An explicit route priority overrides only the named module default."""
    finalizers = build_finalizers({
        "timestamp": {"enabled": True, "priority": 12},
        "tool_rewrite": {"enabled": True},
    })

    assert [type(finalizer) for finalizer in finalizers] == [
        TimestampFinalizer,
        ToolRewriteFinalizer,
        ToolCallFinalizer,
    ]
    assert [finalizer.priority for finalizer in finalizers] == [12, 15, 40]


def test_factory_maps_tls_route_configuration():
    finalizers = build_finalizers({
        "model_tool_loop_stopper": {
            "enabled": True,
            "max_attempts": 5,
            "fuzzy_threshold": 0.9,
            "ab_loop_detection": True,
            "tls_message": "Direct answer.",
            "nudge_message": "No more tools.",
        },
    })
    tls = _by_type(finalizers, TLSFinalizer)[0]
    assert (tls.max_attempts, tls.fuzzy_threshold, tls.detect_ab_loop) == (5, 0.9, True)
    assert tls.tls_message == "Direct answer."
    assert tls.nudge_message == "No more tools."


def test_factory_maps_rls_route_configuration():
    finalizers = build_finalizers({
        "reasoning_loop_stopper": {
            "enabled": True,
            "max_attempts": 4,
            "cache_size": 3,
            "rls_message": "Answer now.",
            "detect_within_stream_loop": True,
        },
    })
    rls = _by_type(finalizers, RLSFinalizer)[0]
    assert (rls.max_attempts, rls.nudge_message, rls.detect_within_stream_loop) == (
        4, "Answer now.", True,
    )
