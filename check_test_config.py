#!/usr/bin/env python3

"""
Check what the actual config says about overflow thresholds.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Checking Configuration for Overflow Thresholds ===")

os.environ['LOG_MODE'] = 'DEBUG'

try:
    # Import the config module that should contain all settings
    from keeprollming.config import (
        SUMMARY_OVERFLOW_IF_PROMPT_CHARS_GT,
        SUMMARY_OVERFLOW_IF_PROMPT_TOKENS_GT,
        DEFAULT_CTX_LEN,
        UPSTREAM_BASE_URL,
        CONFIG
    )
    
    from keeprollming.logger import log
    
    print("Configuration values:")
    print(f"  SUMMARY_OVERFLOW_IF_PROMPT_CHARS_GT: {SUMMARY_OVERFLOW_IF_PROMPT_CHARS_GT}")
    print(f"  SUMMARY_OVERFLOW_IF_PROMPT_TOKENS_GT: {SUMMARY_OVERFLOW_IF_PROMPT_TOKENS_GT}")
    print(f"  DEFAULT_CTX_LEN: {DEFAULT_CTX_LEN}")
    print(f"  UPSTREAM_BASE_URL: {UPSTREAM_BASE_URL}")
    
    # Log these config values for debugging
    log("INFO", "config_values_for_test",
        summary_overflow_chars=SUMMARY_OVERFLOW_IF_PROMPT_CHARS_GT,
        summary_overflow_tokens=SUMMARY_OVERFLOW_IF_PROMPT_TOKENS_GT,
        default_ctx_len=DEFAULT_CTX_LEN,
        upstream_base_url=UPSTREAM_BASE_URL
    )
    
    print("\nThe test sets 'overflow_if_prompt_chars_gt': 2600")
    print("This should be compared to the actual character count in messages.")
    
except Exception as e:
    print(f"Error during config check: {e}")
    import traceback
    traceback.print_exc()

print("=== Config check complete ===")