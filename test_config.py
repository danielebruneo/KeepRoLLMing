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
    
    # Check basic values
    assert CONFIG["upstream_base_url"] == "http://127.0.0.1:1234/v1"
    assert CONFIG["main_model"] == "qwen2.5-3b-instruct"
    assert CONFIG["summary_model"] == "qwen2.5-1.5b-instruct"
    assert CONFIG["passthrough_prefix"] == "pass/"
    
    print("✓ Basic configuration values loaded correctly")
    
    # Check profiles
    assert "quick" in PROFILES
    assert "main" in PROFILES
    assert "deep" in PROFILES
    
    quick_profile = PROFILES["quick"]
    assert quick_profile.main_model == "qwen2.5-3b-instruct"
    assert quick_profile.summary_model == "qwen2.5-1.5b-instruct"
    
    main_profile = PROFILES["main"]
    assert main_profile.main_model == "qwen2.5-v1-7b-instruct"
    assert main_profile.summary_model == "qwen2.5-3b-instruct"
    
    deep_profile = PROFILES["deep"]
    assert deep_profile.main_model == "qwen2.5-27b-instruct"
    assert deep_profile.summary_model == "qwen2.5-7b-instruct"
    
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