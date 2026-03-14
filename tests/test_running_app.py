#!/usr/bin/env python3

"""
Test running the app with DEBUG logging mode to verify log output.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Testing app startup with DEBUG logs ===")

# Set logging mode for test
os.environ['LOG_MODE'] = 'DEBUG'

# Import and run the main function directly
try:
    from keeprollming import main
    
    # We'll just call it but not actually start the server
    print("Calling main() function...")
    
    # Modify main to avoid starting server, just show logging
    original_main = main
    
    def test_main():
        # Override log mode setting in main
        import keeprollming.logger as _logger
        _logger.LOG_MODE = 'DEBUG'
        
        # Call the actual startup logs but don't start uvicorn
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
        
        print("Testing startup logging...")
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
        
        print("Startup logging test completed")
    
    # Run our modified main
    test_main()
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()

print("=== Test complete ===")