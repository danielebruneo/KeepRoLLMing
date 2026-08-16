"""Tests for Pipeline._build_stream_finalizers() chain construction.

Verifies that the V2 finalizer chain is built directly from route
configuration, specifically:
- ToolRewriteFinalizer is included when tool_rewrite is enabled.
- ToolRewriteFinalizer is excluded when tool_rewrite is disabled.
- Priority ordering is correct: ToolRewrite(15) < Timestamp(20) < ToolCall(40).
"""

from __future__ import annotations

import pytest

from keeprollming.orchestrator.pipeline import Pipeline
from keeprollming.filters.tool_rewrite.stream import ToolRewriteFinalizer
from keeprollming.filters.timestamp.stream import TimestampFinalizer
from keeprollming.streaming.finalizers import ToolCallFinalizer
from keeprollming.filters.nudge.stream import NudgeContinuationFinalizer
from keeprollming.filters.tool_loop_stopper.stream import TLSFinalizer
from keeprollming.filters.reasoning_loop_stopper.stream import RLSFinalizer
class TestBuildV2FinalizersChainConstruction:
    """Verify V2 finalizer chain construction from route config."""

    def test_tool_rewrite_enabled_includes_finalizer(self):
        """ToolRewriteFinalizer is present when tool_rewrite is enabled."""
        pipeline = Pipeline(stream_filter_config={"tool_rewrite": {"enabled": True}})
        finalizers = pipeline._build_stream_finalizers()
        tr_finalizers = [
            f for f in finalizers if isinstance(f, ToolRewriteFinalizer)
        ]
        assert len(tr_finalizers) == 1, (
            f"Expected exactly one ToolRewriteFinalizer when filter is "
            f"enabled, got {len(tr_finalizers)}."
        )

    def test_tool_rewrite_disabled_excludes_finalizer(self):
        """ToolRewriteFinalizer is absent when tool_rewrite is disabled."""
        pipeline = Pipeline(stream_filter_config={"tool_rewrite": {"enabled": False}})
        finalizers = pipeline._build_stream_finalizers()
        tr_finalizers = [
            f for f in finalizers if isinstance(f, ToolRewriteFinalizer)
        ]
        assert len(tr_finalizers) == 0, (
            f"Expected zero ToolRewriteFinalizer when filter is disabled, "
            f"got {len(tr_finalizers)}."
        )

    def test_priority_ordering(self):
        """Priority order: ToolRewrite(15) < Timestamp(20) < ToolCall(40)."""
        pipeline = Pipeline(stream_filter_config={
            "timestamp": {"enabled": True},
            "tool_rewrite": {"enabled": True},
        })
        finalizers = pipeline._build_stream_finalizers()

        # Build priority map
        priority_map = {type(f).__name__: f.priority for f in finalizers}

        assert "TimestampFinalizer" in priority_map, (
            "TimestampFinalizer must be in the chain when timestamp is enabled."
        )
        assert "ToolRewriteFinalizer" in priority_map, (
            "ToolRewriteFinalizer must be in the chain when tool_rewrite is enabled."
        )
        assert "ToolCallFinalizer" in priority_map, (
            "ToolCallFinalizer must always be in the chain."
        )

        # Verify priority ordering
        assert priority_map["TimestampFinalizer"] == 20, (
            f"TimestampFinalizer priority must be 20, got {priority_map['TimestampFinalizer']}."
        )
        assert priority_map["ToolRewriteFinalizer"] == 15, (
            f"ToolRewriteFinalizer priority must be 15, got {priority_map['ToolRewriteFinalizer']}."
        )
        assert priority_map["ToolCallFinalizer"] == 40, (
            f"ToolCallFinalizer priority must be 40, got {priority_map['ToolCallFinalizer']}."
        )

        # Verify ordering in the list
        tr_idx = next(i for i, f in enumerate(finalizers) if isinstance(f, ToolRewriteFinalizer))
        ts_idx = next(i for i, f in enumerate(finalizers) if isinstance(f, TimestampFinalizer))
        tc_idx = next(i for i, f in enumerate(finalizers) if isinstance(f, ToolCallFinalizer))

        assert tr_idx < ts_idx < tc_idx, (
            f"Finalizers must be in priority order: ToolRewrite({tr_idx}) < "
            f"Timestamp({ts_idx}) < ToolCall({tc_idx})."
        )

    def test_full_chain_priority_order(self):
        """Full chain: ToolRewrite(15) < Timestamp(20) < ToolCall(40) < Nudge(50)."""
        pipeline = Pipeline(stream_filter_config={
            "timestamp": {"enabled": True},
            "tool_rewrite": {"enabled": True},
            "model_nudge": {"enabled": True},
        })
        finalizers = pipeline._build_stream_finalizers()

        expected_order = [
            ToolRewriteFinalizer,
            TimestampFinalizer,
            ToolCallFinalizer,
            NudgeContinuationFinalizer,
        ]
        actual_types = [type(f) for f in finalizers]

        # Verify all expected finalizers are present
        for expected in expected_order:
            assert expected in actual_types, (
                f"{expected.__name__} must be in the finalizer chain."
            )

        # Verify ordering
        for i in range(len(expected_order) - 1):
            idx_prev = actual_types.index(expected_order[i])
            idx_next = actual_types.index(expected_order[i + 1])
            assert idx_prev < idx_next, (
                f"{expected_order[i].__name__} (idx={idx_prev}) must come before "
                f"{expected_order[i + 1].__name__} (idx={idx_next})."
            )

    def test_config_mapping_supported_patterns(self):
        """tool_rewrite supported_patterns maps to the V2 finalizer."""
        pipeline = Pipeline(stream_filter_config={
            "tool_rewrite": {
                "enabled": True,
                "supported_patterns": ["nested", "separate"],
            },
        })
        finalizers = pipeline._build_stream_finalizers()
        tr_finalizers = [
            f for f in finalizers if isinstance(f, ToolRewriteFinalizer)
        ]
        assert len(tr_finalizers) == 1
        assert tr_finalizers[0].supported_patterns == ["nested", "separate"]
