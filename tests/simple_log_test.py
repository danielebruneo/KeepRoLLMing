#!/usr/bin/env python3

"""
Simple direct test of logger functionality.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Simple log function testing ===")

# Set logging mode explicitly
os.environ['LOG_MODE'] = 'DEBUG'

try:
    # Import the logger module directly
    from keeprollming.logger import log
    
    print("Testing different log levels...")
    
    # Test basic INFO level
    log("INFO", "test_info_message", test_field="info_value")
    
    # Test DEBUG level
    log("DEBUG", "test_debug_message", debug_data={"key": "value"})
    
    # Test WARNING level
    log("WARNING", "test_warning_message", warning_level=5)
    
    # Test ERROR level  
    log("ERROR", "test_error_message", error_code="ERR-001")
    
    print("All logs output successfully!")
    
except Exception as e:
    print(f"Error during test: {e}")
    import traceback
    traceback.print_exc()

print("=== Simple test complete ===")