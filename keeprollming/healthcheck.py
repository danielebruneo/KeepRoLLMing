"""E2E Health Check for KeepRoLLMing orchestrator.

This module provides tools to perform live health checks on configured routes
by making actual API calls to backends and verifying responses.

Usage:
    from keeprollming.healthcheck import run_health_check
    
    results = run_health_check(config, timeout=10)
    results.print_report()
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from keeprollming.config import CONFIG
from keeprollming.routing import BUILTIN_ROUTES, Route, get_route_settings, resolve_route


@dataclass
class HealthCheckResult:
    """Result of a health check for a single route."""
    route_name: str
    status: str  # "healthy", "unhealthy", "timeout", "error"
    upstream_url: Optional[str] = None
    expected_model: Optional[str] = None
    actual_model: Optional[str] = None
    latency_ms: Optional[float] = None
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class HealthCheckResults:
    """Container for multiple health check results."""
    
    def __init__(self):
        self.results: List[HealthCheckResult] = []
        self.summary = {
            "total": 0,
            "healthy": 0,
            "unhealthy": 0,
            "timeout": 0,
            "error": 0,
        }
    
    def add_result(self, result: HealthCheckResult):
        self.results.append(result)
        self.summary["total"] += 1
        
        if result.status == "healthy":
            self.summary["healthy"] += 1
        elif result.status == "unhealthy":
            self.summary["unhealthy"] += 1
        elif result.status == "timeout":
            self.summary["timeout"] += 1
        else:
            self.summary["error"] += 1
    
    @property
    def is_healthy(self) -> bool:
        return self.summary["unhealthy"] == 0 and self.summary["timeout"] == 0 and self.summary["error"] == 0
    
    def print_report(self):
        """Print a human-readable health check report."""
        print("=" * 60)
        print("KEEPROLLMING HEALTH CHECK REPORT")
        print("=" * 60)
        
        print(f"\nSummary:")
        print(f"  Total routes checked: {self.summary['total']}")
        print(f"  ✓ Healthy: {self.summary['healthy']}")
        print(f"  ✗ Unhealthy: {self.summary['unhealthy']}")
        print(f"  ⏱ Timeout: {self.summary['timeout']}")
        print(f"  ❌ Error: {self.summary['error']}")
        
        if self.is_healthy:
            print("\n✓ All routes are healthy!")
        else:
            print("\n✗ Some routes have issues:")
        
        print(f"\nDetailed Results:")
        for result in self.results:
            status_icon = {
                "healthy": "✓",
                "unhealthy": "✗",
                "timeout": "⏱",
                "error": "❌",
            }.get(result.status, "?")
            
            print(f"\n{status_icon} {result.route_name}")
            print(f"  Status: {result.status.upper()}")
            if result.upstream_url:
                print(f"  Upstream: {result.upstream_url}")
            if result.expected_model:
                print(f"  Expected model: {result.expected_model}")
            if result.actual_model:
                print(f"  Actual model: {result.actual_model}")
            if result.latency_ms is not None:
                print(f"  Latency: {result.latency_ms:.2f} ms")
            if result.error_message:
                print(f"  Error: {result.error_message}")
            if result.details:
                for key, value in result.details.items():
                    print(f"  {key}: {value}")


def run_health_check(
    config: Dict[str, Any],
    timeout: int = 10,
    max_concurrent: int = 5,
    verbose: bool = False,
) -> HealthCheckResults:
    """
    Run health checks on all non-private routes.
    
    Args:
        config: Configuration dictionary
        timeout: Timeout in seconds for each request
        max_concurrent: Maximum number of concurrent requests
        verbose: If True, print progress during checks
        
    Returns:
        HealthCheckResults object with all results
    """
    from keeprollming.config import load_user_routes
    
    # Load routes
    user_routes = load_user_routes(config)
    all_routes = user_routes + BUILTIN_ROUTES
    
    # Filter to non-private routes only
    checkable_routes = [
        route for route in all_routes 
        if not getattr(route, "is_private", False) and not route.name.startswith("builtin/")
    ]
    
    if verbose:
        print(f"Checking {len(checkable_routes)} routes...")
    
    # Run async health check
    return asyncio.run(_run_health_check_async(checkable_routes, all_routes, timeout, max_concurrent, verbose))


async def _run_health_check_async(
    checkable_routes: list[Route],
    all_routes: list[Route],
    timeout: int,
    max_concurrent: int,
    verbose: bool,
) -> HealthCheckResults:
    """Internal async implementation of health check."""
    results = HealthCheckResults()
    
    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def check_route(route: Route):
        """Check a single route's health."""
        async with semaphore:
            try:
                from keeprollming.routing import get_route_settings, resolve_route
                
                # Resolve route settings
                resolved_route, backend_model = resolve_route(route.name, all_routes)
                settings = get_route_settings(resolved_route, backend_model)
                
                upstream_url = settings.get("upstream_url")
                if not upstream_url:
                    return HealthCheckResult(
                        route_name=route.name,
                        status="error",
                        error_message="No upstream URL configured"
                    )
                
                # Build test payload - use backend_model (actual model name) not route name
                test_payload = {
                    "model": settings.get("backend_model"),
                    "messages": [{"role": "user", "content": "Health check"}],
                    "max_tokens": 10,
                }
                
                # Make request
                start_time = time.time()
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{upstream_url}/v1/chat/completions",
                        json=test_payload,
                        headers={"Content-Type": "application/json"},
                    )
                
                latency_ms = (time.time() - start_time) * 1000
                
                # Check response
                if response.status_code != 200:
                    return HealthCheckResult(
                        route_name=route.name,
                        status="unhealthy",
                        upstream_url=upstream_url,
                        expected_model=settings.get("main_model"),
                        error_message=f"HTTP {response.status_code}: {response.text[:100]}",
                        latency_ms=latency_ms,
                    )
                
                # Parse response
                try:
                    data = response.json()
                    actual_model = data.get("model")
                    
                    if "error" in data:
                        return HealthCheckResult(
                            route_name=route.name,
                            status="unhealthy",
                            upstream_url=upstream_url,
                            expected_model=settings.get("main_model"),
                            error_message=data["error"].get("message", str(data["error"])),
                            latency_ms=latency_ms,
                        )
                    
                    return HealthCheckResult(
                        route_name=route.name,
                        status="healthy",
                        upstream_url=upstream_url,
                        expected_model=settings.get("main_model"),
                        actual_model=actual_model,
                        latency_ms=latency_ms,
                    )
                except Exception as e:
                    return HealthCheckResult(
                        route_name=route.name,
                        status="unhealthy",
                        upstream_url=upstream_url,
                        expected_model=settings.get("main_model"),
                        error_message=f"Failed to parse response: {str(e)}",
                        latency_ms=latency_ms,
                    )
                    
            except httpx.TimeoutException:
                return HealthCheckResult(
                    route_name=route.name,
                    status="timeout",
                    error_message=f"Request timed out after {timeout}s"
                )
            except httpx.ConnectError as e:
                return HealthCheckResult(
                    route_name=route.name,
                    status="error",
                    error_message=f"Connection failed: {str(e)}"
                )
            except Exception as e:
                return HealthCheckResult(
                    route_name=route.name,
                    status="error",
                    error_message=f"Unexpected error: {str(e)}"
                )
    
    # Run all checks concurrently (within concurrency limit)
    tasks = [check_route(route) for route in checkable_routes]
    results_list = await asyncio.gather(*tasks)
    
    for result in results_list:
        results.add_result(result)
    
    return results


def quick_healthcheck(timeout: int = 10) -> bool:
    """
    Quick health check with summary output.
    
    Args:
        timeout: Timeout in seconds for each request
        
    Returns:
        True if all routes are healthy, False otherwise
    """
    results = run_health_check(CONFIG, timeout=timeout, verbose=True)
    results.print_report()
    
    return results.is_healthy
