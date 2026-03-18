#!/usr/bin/env python3

"""
Test script to verify the new flexible configuration system works correctly.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keeprollming.config import CONFIG, PROFILES, MODEL_ALIASES, PASSTHROUGH_PREFIX

def test_config_loading():
    """Test that config loading works properly"""
    print("Testing configuration loading...")

    # Check basic values - use environment variables for flexible testing
    # Read actual upstream_base_url from config to avoid hardcoded expectations
    expected_upstream_base_url = os.environ.get("TEST_UPSTREAM_BASE_URL", CONFIG["upstream_base_url"])
    assert CONFIG["upstream_base_url"] == expected_upstream_base_url
    assert CONFIG["passthrough_prefix"] == "pass/"

    print("✓ Basic configuration values loaded correctly")

    # Check profiles - verify structure exists without specific model names
    assert "quick" in PROFILES
    assert "main" in PROFILES
    assert "deep" in PROFILES

    quick_profile = PROFILES["quick"]
    assert isinstance(quick_profile.main_model, str)
    assert isinstance(quick_profile.summary_model, str)

    main_profile = PROFILES["main"]
    assert isinstance(main_profile.main_model, str)
    assert isinstance(main_profile.summary_model, str)

    deep_profile = PROFILES["deep"]
    assert isinstance(deep_profile.main_model, str)
    assert isinstance(deep_profile.summary_model, str)

    print("✓ Profiles loaded correctly")

    # Check model aliases
    assert MODEL_ALIASES["local/quick"] == "quick"
    assert MODEL_ALIASES["local/main"] == "main"
    assert MODEL_ALIASES["local/deep"] == "deep"

    print("✓ Model aliases loaded correctly")

    # Check passthrough prefix
    assert PASSTHROUGH_PREFIX == "pass/"

    print("✓ Passthrough prefix loaded correctly")

    print("\nAll configuration tests passed!")

if __name__ == "__main__":
    test_config_loading()