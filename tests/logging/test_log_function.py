#!/usr/bin/env python3

"""
Simple test to verify logging functionality works correctly.
This directly tests the 'log' function from keeprollming.logger module.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Testing log function ===")
from keeprollming.logger import log

# Test the log function directly
print("\nTesting basic log calls...")
log("INFO", "test_message", key1="value1", key2="value2")
log("DEBUG", "debug_message", debug_info="some_debug_data")
log("WARNING", "warning_message", warning_level=3)

print("\n=== Log test complete ===")

log("INFO", "nested_test", data={"nested": {"value": 42}})
print("\n=== Log test complete ===")
