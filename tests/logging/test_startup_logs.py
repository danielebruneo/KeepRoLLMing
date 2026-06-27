#!/usr/bin/env python3

"""
Test to demonstrate actual application startup logs with DEBUG mode.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Testing Application Startup Logs ===")

# Set logging mode explicitly for this test
os.environ['LOG_MODE'] = 'DEBUG'

try:
    # Import config and logger modules
    from keeprollming.config import (
        BASE_MAIN_MODEL,
        BASE_SUMMARY_MODEL,
        DEFAULT_CTX_LEN,
        DEEP_MAIN_MODEL,
        DEEP_SUMMARY_MODEL,
        MAIN_MODEL,
        QUICK_MAIN_MODEL,
        QUICK_SUMMARY_MODEL,
        SUMMARY_MODEL,
        UPSTREAM_BASE_URL,
        CONFIG
    )
    
    from keeprollming.logger import log
    
    print("Simulating startup logging...")
    
    # This mimics what happens in main() function when the app starts up
    log(
        "INFO",
        "startup",
        upstream=UPSTREAM_BASE_URL,
        main_model=MAIN_MODEL,
        summary_model=SUMMARY_MODEL,
        log_mode="DEBUG",
        profiles={
            "quick": {"main": QUICK_MAIN_MODEL, "summary": QUICK_SUMMARY_MODEL},
            "main": {"main": BASE_MAIN_MODEL, "summary": BASE_SUMMARY_MODEL},
            "deep": {"main": DEEP_MAIN_MODEL, "summary": DEEP_SUMMARY_MODEL},
        },
        default_ctx=DEFAULT_CTX_LEN,
    )
    
    print("Startup logs displayed successfully!")
    
except Exception as e:
    print(f"Error during test: {e}")
    import traceback
    traceback.print_exc()

print("=== Startup log test complete ===")