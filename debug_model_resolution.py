#!/usr/bin/env python3

"""
Debug the model resolution and fake backend logic that causes summary calls not to be recognized
"""

import os
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

os.environ['LOG_MODE'] = 'DEBUG'

def main():
    print("=== Debug Model Resolution ===")
    
    # Import modules needed for analysis
    from keeprollming.config import (
        BASE_MAIN_MODEL,
        BASE_SUMMARY_MODEL,
        QUICK_MAIN_MODEL,
        QUICK_SUMMARY_MODEL,
        SUMMARY_MODEL,
        MODEL_ALIASES,
        PROFILES,
        resolve_profile_and_models
    )
    
    print("Configuration loaded:")
    print(f"  MAIN_MODEL: {BASE_MAIN_MODEL}")
    print(f"  SUMMARY_MODEL: {SUMMARY_MODEL}")
    print(f"  QUICK_MAIN_MODEL: {QUICK_MAIN_MODEL}")
    print(f"  QUICK_SUMMARY_MODEL: {QUICK_SUMMARY_MODEL}")
    
    # From the test:
    # backend_target.client_model_summary = 'local/quick'
    client_model = "local/quick"
    
    profile, upstream_main_model, summary_model, is_passthrough = resolve_profile_and_models(client_model)
    
    print(f"\nModel resolution for '{client_model}':")
    print(f"  Profile: {profile}")
    print(f"  Upstream main model: {upstream_main_model}")
    print(f"  Summary model: {summary_model}")
    print(f"  Is passthrough: {is_passthrough}")
    
    # Check MODEL_ALIASES
    print(f"\nMODEL_ALIASES mapping:")
    for k, v in MODEL_ALIASES.items():
        print(f"  '{k}' -> '{v}'")
        
    # Test the fake backend logic with this model name
    from tests.e2e.fake_backend import _kind_for_payload
    
    messages = [
        {"role": "user", "content": "test message"},
        {"role": "assistant", "content": "test reply"}
    ]
    
    print(f"\nTest _kind_for_payload with summary_model '{summary_model}':")
    kind = _kind_for_payload(summary_model, messages)
    print(f"  Kind result: {kind}")
    
    # Check if it matches exactly 'summary-model'
    expected = "summary-model"
    is_exact_match = (summary_model == expected)
    print(f"  Exact match with '{expected}': {is_exact_match}")
    
    print("\n=== Problem Analysis ===")
    if not is_exact_match:
        print("PROBLEM: The summary model name 'qwen2.5-1.5b-instruct' does NOT match exactly 'summary-model'")
        print("This means fake backend logic will classify this as a chat request, not a summary request!")
        print("So no calls are counted in the 'summary' category.")

if __name__ == "__main__":
    main()