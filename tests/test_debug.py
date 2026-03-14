#!/usr/bin/env python3

import os
import sys
from pathlib import Path

# Add project root to path 
sys.path.insert(0, str(Path(__file__).parent))

print("=== Testing debug setup ===")
print(f"LOG_MODE environment: {os.environ.get('LOG_MODE', 'NOT SET')}")
print(f"Current working directory: {Path.cwd()}")

from keeprollming.config import CONFIG

# Check config values
print("Summary mode:", CONFIG.get('summary_mode'))
print("Cache enabled:", CONFIG.get('summary_cache_enabled'))
print("Cache dir:", CONFIG.get('summary_cache_dir'))

# Try to run a quick test with debug logging 
os.environ['LOG_MODE'] = 'DEBUG'
print("Set LOG_MODE=DEBUG")

try:
    from keeprollming.app import app
    print("App imported successfully")
except Exception as e:
    print(f"Error importing app: {e}")
    
print("=== Debug setup complete ===")