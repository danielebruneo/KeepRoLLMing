"""Integration tests for health check functionality."""

import pytest
from typing import Dict, Any

from keeprollming.healthcheck import (
    HealthCheckResult,
    HealthCheckResults,
    quick_healthcheck,
)


# ============================================================================
# Tests for HealthCheckResult and HealthCheckResults
# ============================================================================

def test_health_check_result_creation():
    """Test creating a health check result."""
    result = HealthCheckResult(
        route_name="test/route",
        status="healthy",
        upstream_url="http://test.com",
        expected_model="test-model",
        actual_model="test-model",
        latency_ms=100.5,
    )
    
    assert result.route_name == "test/route"
    assert result.status == "healthy"
    assert result.upstream_url == "http://test.com"
    assert result.latency_ms == 100.5


def test_health_check_result_unhealthy():
    """Test creating an unhealthy health check result."""
    result = HealthCheckResult(
        route_name="test/route",
        status="unhealthy",
        upstream_url="http://test.com",
        expected_model="test-model",
        error_message="Connection refused",
    )
    
    assert result.status == "unhealthy"
    assert result.error_message == "Connection refused"


def test_health_check_results_add():
    """Test adding results to HealthCheckResults."""
    results = HealthCheckResults()
    
    result1 = HealthCheckResult(
        route_name="route1",
        status="healthy",
        upstream_url="http://test1.com",
    )
    result2 = HealthCheckResult(
        route_name="route2",
        status="unhealthy",
        upstream_url="http://test2.com",
    )
    
    results.add_result(result1)
    results.add_result(result2)
    
    assert len(results.results) == 2
    assert results.summary["total"] == 2


def test_health_check_results_summary():
    """Test generating summary statistics."""
    results = HealthCheckResults()
    
    for i in range(5):
        results.add_result(HealthCheckResult(
            route_name=f"route{i}",
            status="healthy",
            upstream_url="http://test.com",
        ))
    
    for i in range(3):
        results.add_result(HealthCheckResult(
            route_name=f"error{i}",
            status="error",
            error_message="Connection failed",
        ))
    
    # Check summary counts
    assert results.summary["total"] == 8
    assert results.summary["healthy"] == 5
    assert results.summary["error"] == 3
    
    # Check that is_healthy returns False when there are unhealthy routes
    assert results.is_healthy == False


# ============================================================================
# Tests for quick_healthcheck
# ============================================================================

def test_quick_healthcheck_structure():
    """Test that quick_healthcheck returns a boolean."""
    # Note: This will actually try to connect to backends, so we just check it runs
    result = quick_healthcheck(timeout=1)
    assert isinstance(result, bool)


# ============================================================================
# Integration tests with real config
# ============================================================================

def test_healthcheck_with_real_config():
    """Test health check against actual config.yaml."""
    from keeprollming.config import load_user_routes, CONFIG
    from keeprollming.routing import BUILTIN_ROUTES
    from keeprollming.healthcheck import run_health_check
    
    user_routes = load_user_routes(CONFIG)
    all_routes = user_routes + BUILTIN_ROUTES
    routes_by_name = {route.name: route for route in all_routes}
    
    # Run health check with very short timeout to avoid hanging
    results = run_health_check(CONFIG, timeout=1, verbose=False)
    
    # Should complete and return results object
    assert isinstance(results, HealthCheckResults)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
