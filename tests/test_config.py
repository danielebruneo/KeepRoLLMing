#!/usr/bin/env python3

"""
Test script to verify the new flexible configuration system works correctly.
Tests the routing-based configuration with inheritance support.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keeprollming.config import load_user_routes
from keeprollming.routing import resolve_route, BUILTIN_ROUTES
import yaml


def test_config_loading():
    """Test that config loading works properly with new routing system"""
    print("Testing configuration loading...")

    # Load the test config file
    config_path = os.path.join(os.path.dirname(__file__), "config.test.yaml")
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Check upstream_base_url
    assert config["upstream_base_url"] == "http://localhost:9999/v1"
    
    # Load user routes
    routes = load_user_routes(config)
    routes_by_name = {r.name: r for r in routes}

    print(f"✓ Loaded {len(routes)} user-defined routes")

    # Check that expected routes exist
    assert "quick" in routes_by_name
    assert "main" in routes_by_name
    assert "deep" in routes_by_name
    assert "passthrough" in routes_by_name

    print("✓ All expected routes loaded correctly")

    # Test route resolution for quick profile
    resolved, backend = resolve_route("local/quick", routes)
    assert resolved.main_model == "test-quick-model"
    assert resolved.summary_model == "test-summary-1.5b"
    assert resolved.ctx_len == 8192
    print("✓ Route resolution works for quick profile")

    # Test route resolution for main profile
    resolved, backend = resolve_route("local/main", routes)
    assert resolved.main_model == "test-main-model"
    assert resolved.summary_model == "test-summary-3b"
    assert resolved.ctx_len == 16384
    print("✓ Route resolution works for main profile")

    # Test route resolution for deep profile
    resolved, backend = resolve_route("local/deep", routes)
    assert resolved.main_model == "test-deep-model"
    assert resolved.summary_model == "test-summary-7b"
    assert resolved.ctx_len == 32768
    print("✓ Route resolution works for deep profile")

    # Test passthrough route
    resolved, backend = resolve_route("pass/openai/gpt-4", routes)
    assert resolved.passthrough_enabled is True
    print("✓ Passthrough route works correctly")

    # Test fallback for unknown route (uses hardcoded DEFAULT_FALLBACK_ROUTE)
    resolved, backend = resolve_route("unknown/model", routes)
    # Fallback uses default values from routing.py, not config.yaml
    assert resolved.main_model == "qwen2.5-v1-7b-instruct"
    print("✓ Fallback route works correctly (uses hardcoded defaults)")

    # Check that built-in routes still exist
    assert len(BUILTIN_ROUTES) > 0
    print(f"✓ {len(BUILTIN_ROUTES)} built-in routes available")

    print("\nAll configuration tests passed!")


if __name__ == "__main__":
    test_config_loading()
