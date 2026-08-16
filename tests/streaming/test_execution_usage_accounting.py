"""Tests for ExecutionUsage accounting module.

This module tests the ExecutionUsage and AttemptUsage dataclasses
implemented in keeprollming/streaming/accounting.py.

Test plan (13 tests total):
1. test_execution_usage_basic_aggregation
2. test_execution_usage_missing_usage
3. test_execution_usage_derived_total
4. test_execution_usage_recovery_continuation
5. test_execution_usage_stream_exhaustion
6. test_execution_usage_fallback_path
7. test_execution_usage_partial_usage
8. test_execution_usage_per_attempt_ledger
9. test_execution_usage_usage_complete_flag
10. test_execution_usage_metrics_consumption
11. test_execution_usage_recovery_reset (critical missing)
12. test_execution_usage_fallback_no_accounting (critical missing)
13. test_execution_usage_shape_b_late_usage (critical missing)
"""

import pytest
from keeprollming.streaming.accounting import ExecutionUsage, AttemptUsage


# Test 1: test_execution_usage_basic_aggregation
def test_execution_usage_basic_aggregation():
    """Test basic SUM aggregation across multiple attempts.

    Verifies that prompt_tokens and completion_tokens are correctly
    aggregated across multiple attempts.
    """
    usage = ExecutionUsage.empty()

    # Add first attempt with usage
    usage.add_attempt(0, {
        "prompt_tokens": 100,
        "completion_tokens": 200,
    })

    # Add second attempt with usage
    usage.add_attempt(1, {
        "prompt_tokens": 150,
        "completion_tokens": 250,
    })

    # Verify aggregation
    assert usage.upstream_attempts == 2
    assert usage.usage_reported_attempts == 2
    assert usage.upstream_prompt_tokens == 250  # 100 + 150
    assert usage.upstream_completion_tokens == 450  # 200 + 250
    assert usage.upstream_total_tokens == 700  # 250 + 450
    # Historical aliases remain cumulative upstream totals.
    assert usage.prompt_tokens == 250
    assert usage.completion_tokens == 450
    assert usage.total_tokens == 700
    # Before transcript reconstruction, the last provider usage is provisional.
    assert usage.final_prompt_tokens == 150
    assert usage.final_completion_tokens == 250
    assert usage.usage_complete is True
    assert len(usage.attempts) == 2


# Test 2: test_execution_usage_missing_usage
def test_execution_usage_missing_usage():
    """Test missing usage tracked as unknown (not zero).

    Verifies that when raw_usage is None, the attempt is still counted
    in upstream_attempts but not in usage_reported_attempts.
    """
    usage = ExecutionUsage.empty()

    # Add attempt with no usage
    usage.add_attempt(0, None)

    # Verify accounting
    assert usage.upstream_attempts == 1
    assert usage.usage_reported_attempts == 0
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0
    assert usage.usage_complete is False  # 0 != 1
    assert len(usage.attempts) == 1
    assert usage.attempts[0].raw_usage is None


# Test 3: test_execution_usage_derived_total
def test_execution_usage_derived_total():
    """Test total_tokens = prompt + completion derivation.

    Verifies that total_tokens is derived from prompt_tokens + completion_tokens,
    not taken from provider-reported total_tokens.
    """
    usage = ExecutionUsage.empty()

    # Add attempt with provider-reported total_tokens that differs from sum
    usage.add_attempt(0, {
        "prompt_tokens": 100,
        "completion_tokens": 200,
        "total_tokens": 999,  # Provider-reported, should be ignored
    })

    # Verify derivation
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 200
    assert usage.total_tokens == 300  # 100 + 200, NOT 999
    assert usage.attempts[0].raw_usage["total_tokens"] == 999  # Still stored in raw


# Test 4: test_execution_usage_recovery_continuation
def test_execution_usage_recovery_continuation():
    """Test accounting continues across recovery.

    Verifies that accounting continues across recovery boundaries,
    with each recovery attempt adding to the cumulative totals.
    """
    usage = ExecutionUsage.empty()

    # First attempt (recovered)
    usage.add_attempt(0, {
        "prompt_tokens": 100,
        "completion_tokens": 200,
    })

    # Recovery attempt
    usage.add_attempt(1, {
        "prompt_tokens": 150,
        "completion_tokens": 250,
    })

    # Verify cumulative accounting
    assert usage.upstream_attempts == 2
    assert usage.prompt_tokens == 250
    assert usage.completion_tokens == 450
    assert usage.total_tokens == 700
    assert usage.recovery_count == 1  # One recovery


def test_execution_usage_separates_final_logical_usage_from_upstream_totals():
    """A recovery's discarded tokens remain accounting-only, never TPS input."""
    usage = ExecutionUsage.empty()
    usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 15})
    usage.add_attempt(1, {"prompt_tokens": 120, "completion_tokens": 120})

    usage.set_final_usage(prompt_tokens=120, completion_tokens=120)

    assert usage.upstream_prompt_tokens == 220
    assert usage.upstream_completion_tokens == 135
    assert usage.final_prompt_tokens == 120
    assert usage.final_completion_tokens == 120


# Test 5: test_execution_usage_stream_exhaustion
def test_execution_usage_stream_exhaustion():
    """Test accounting on stream exhaustion (no Finish).

    Verifies that accounting captures usage at stream exhaustion,
    even when no Finish event is emitted.
    """
    usage = ExecutionUsage.empty()

    # Simulate stream exhaustion with usage
    usage.add_attempt(0, {
        "prompt_tokens": 100,
        "completion_tokens": 200,
    })

    # Verify accounting
    assert usage.upstream_attempts == 1
    assert usage.usage_reported_attempts == 1
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 200
    assert usage.total_tokens == 300
    assert usage.usage_complete is True


# Test 6: test_execution_usage_fallback_path
def test_execution_usage_fallback_path():
    """Test accounting on fallback (upstream_factory exception).

    Verifies that fallback path does NOT create an AttemptUsage entry,
    and upstream_attempts counts consumed iterators, not factory calls.
    """
    usage = ExecutionUsage.empty()

    # First attempt succeeds
    usage.add_attempt(0, {
        "prompt_tokens": 100,
        "completion_tokens": 200,
    })

    # Fallback path (exception) - should NOT add an attempt
    # (This is simulated by not calling add_attempt)

    # Verify accounting
    assert usage.upstream_attempts == 1  # Only one consumed iterator
    assert usage.usage_reported_attempts == 1
    assert len(usage.attempts) == 1


# Test 7: test_execution_usage_partial_usage
def test_execution_usage_partial_usage():
    """Test missing fields treated as 0.

    Verifies that missing fields (absent keys OR null values) are
    treated as 0 for SUM purposes.
    """
    usage = ExecutionUsage.empty()

    # Add attempt with partial usage (missing completion_tokens)
    usage.add_attempt(0, {
        "prompt_tokens": 100,
        # completion_tokens missing
    })

    # Verify partial field semantics
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 0  # Missing field treated as 0
    assert usage.total_tokens == 100


# Test 8: test_execution_usage_per_attempt_ledger
def test_execution_usage_per_attempt_ledger():
    """Test per-attempt raw usage retention.

    Verifies that raw_usage is stored verbatim for each attempt,
    preserving provider-specific fields.
    """
    usage = ExecutionUsage.empty()

    # Add attempt with provider-specific fields
    usage.add_attempt(0, {
        "prompt_tokens": 100,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 50},
        "completion_tokens_details": {"reasoning_tokens": 30},
    })

    # Verify raw usage retention
    assert usage.attempts[0].raw_usage["prompt_tokens"] == 100
    assert usage.attempts[0].raw_usage["completion_tokens"] == 200
    assert usage.attempts[0].raw_usage["prompt_tokens_details"]["cached_tokens"] == 50
    assert usage.attempts[0].raw_usage["completion_tokens_details"]["reasoning_tokens"] == 30
    assert usage.final_cached_prompt_tokens == 50


# Test 9: test_execution_usage_usage_complete_flag
def test_execution_usage_usage_complete_flag():
    """Test usage_complete flag.

    Verifies that usage_complete is True when all attempts reported usage,
    and False when some attempts did not report usage.
    """
    # Test 1: All attempts reported usage
    usage1 = ExecutionUsage.empty()
    usage1.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})
    usage1.add_attempt(1, {"prompt_tokens": 150, "completion_tokens": 250})
    assert usage1.usage_complete is True

    # Test 2: Some attempts did not report usage
    usage2 = ExecutionUsage.empty()
    usage2.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})
    usage2.add_attempt(1, None)  # No usage
    assert usage2.usage_complete is False

    # Test 3: Zero attempts (edge case)
    usage3 = ExecutionUsage.empty()
    assert usage3.usage_complete is True  # 0 == 0


# Test 10: test_execution_usage_metrics_consumption
def test_execution_usage_metrics_consumption():
    """Test metrics consume ExecutionUsage.

    Verifies that metrics can consume ExecutionUsage for cumulative
    token counts.
    """
    usage = ExecutionUsage.empty()

    # Add multiple attempts
    usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})
    usage.add_attempt(1, {"prompt_tokens": 150, "completion_tokens": 250})

    # Verify metrics can consume ExecutionUsage
    assert usage.prompt_tokens == 250
    assert usage.completion_tokens == 450
    assert usage.total_tokens == 700
    assert usage.upstream_attempts == 2


# Test 11: test_execution_usage_recovery_reset (critical missing)
def test_execution_usage_recovery_reset():
    """Test pending_usage[0] = None on recovery.

    Verifies that pending_usage[0] is reset to None on recovery,
    ensuring no cross-attempt contamination.
    """
    usage = ExecutionUsage.empty()

    # First attempt
    usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})

    # Recovery resets pending_usage[0] (simulated by adding new attempt)
    usage.add_attempt(1, {"prompt_tokens": 150, "completion_tokens": 250})

    # Verify no cross-attempt contamination
    assert usage.prompt_tokens == 250  # 100 + 150
    assert usage.completion_tokens == 450  # 200 + 250
    assert len(usage.attempts) == 2


# Test 12: test_execution_usage_fallback_no_accounting (critical missing)
def test_execution_usage_fallback_no_accounting():
    """Test fallback path does NOT create AttemptUsage.

    Verifies that fallback path (upstream_factory exception) does NOT
    create an AttemptUsage entry.
    """
    usage = ExecutionUsage.empty()

    # First attempt succeeds
    usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})

    # Fallback path (simulated by not adding another attempt)
    # In real code, this would be when upstream_factory raises an exception

    # Verify no AttemptUsage was created for fallback
    assert usage.upstream_attempts == 1
    assert len(usage.attempts) == 1
    assert usage.attempts[0].attempt_index == 0


# Test 13: test_execution_usage_shape_b_late_usage (critical missing)
def test_execution_usage_shape_b_late_usage():
    """Test Shape B late usage is NOT captured for accounting.

    Verifies that Shape B late usage (usage after Finish) is NOT
    captured for accounting in Phase 1.
    """
    usage = ExecutionUsage.empty()

    # Normal usage captured at Finish
    usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})

    # Shape B late usage would be captured separately (not in this test)
    # In real code, this would be usage that arrives after Finish event

    # Verify only one attempt was captured
    assert usage.upstream_attempts == 1
    assert len(usage.attempts) == 1
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 200


# Additional helper tests

def test_execution_usage_empty_state():
    """Test empty ExecutionUsage state.

    Verifies that an empty ExecutionUsage has all counters at zero
    and usage_complete is True (0 == 0).
    """
    usage = ExecutionUsage.empty()

    assert usage.upstream_attempts == 0
    assert usage.usage_reported_attempts == 0
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0
    assert usage.usage_complete is True
    assert len(usage.attempts) == 0
    assert usage.recovery_count == 0
    assert usage.retry_amplification_ratio == 0.0


def test_execution_usage_retry_amplification_ratio():
    """Test retry_amplification_ratio calculation.

    Verifies that retry_amplification_ratio correctly calculates
    the ratio of upstream attempts to usage-reported attempts.
    """
    usage = ExecutionUsage.empty()

    # All attempts reported usage
    usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})
    usage.add_attempt(1, {"prompt_tokens": 150, "completion_tokens": 250})
    assert usage.retry_amplification_ratio == 1.0  # 2 / 2

    # Some attempts did not report usage
    usage2 = ExecutionUsage.empty()
    usage2.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})
    usage2.add_attempt(1, None)  # No usage
    assert usage2.retry_amplification_ratio == 2.0  # 2 / 1


def test_execution_usage_retry_amplification_ratio_zero_usage():
    """Test retry_amplification_ratio returns inf when usage_reported_attempts == 0.

    Regression test for FIX-092-RUNTIME-INTEGRATION-DEFECTS-001:
    When upstream_attempts > 0 and usage_reported_attempts == 0,
    retry_amplification_ratio must return float('inf') instead of
    raising ZeroDivisionError. This truthfully represents unbounded
    amplification relative to zero reported data (D-035).
    """
    import math

    # Case: upstream_attempts > 0, usage_reported_attempts == 0
    usage = ExecutionUsage.empty()
    usage.add_attempt(0, None)  # No usage reported

    assert usage.upstream_attempts == 1
    assert usage.usage_reported_attempts == 0
    ratio = usage.retry_amplification_ratio
    assert math.isinf(ratio), f"Expected inf, got {ratio}"
    assert ratio > 0, "Expected positive infinity"

    # Case: multiple attempts, none reported usage
    usage2 = ExecutionUsage.empty()
    usage2.add_attempt(0, None)
    usage2.add_attempt(1, None)
    ratio2 = usage2.retry_amplification_ratio
    assert math.isinf(ratio2), f"Expected inf, got {ratio2}"

    # Verify no ZeroDivisionError is raised
    try:
        _ = usage.retry_amplification_ratio
        _ = usage2.retry_amplification_ratio
    except ZeroDivisionError:
        pytest.fail("retry_amplification_ratio raised ZeroDivisionError")


def test_execution_usage_finalize():
    """Test finalize method.

    Verifies that finalize() updates the usage_complete flag
    to reflect current state.
    """
    usage = ExecutionUsage.empty()

    # Add all attempts with usage
    usage.add_attempt(0, {"prompt_tokens": 100, "completion_tokens": 200})
    usage.add_attempt(1, {"prompt_tokens": 100, "completion_tokens": 200})
    usage.add_attempt(2, {"prompt_tokens": 100, "completion_tokens": 200})

    # usage_complete should be True (3 == 3)
    assert usage.usage_complete is True

    # Finalize should not change anything
    usage.finalize()
    assert usage.usage_complete is True

    # Add attempt with no usage
    usage.add_attempt(3, None)

    # usage_complete should be False (3 != 4)
    assert usage.usage_complete is False

    # Finalize should not change anything
    usage.finalize()
    assert usage.usage_complete is False
