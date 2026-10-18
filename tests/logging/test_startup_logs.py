#!/usr/bin/env python3

"""
Test to demonstrate actual application startup logs with DEBUG mode.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

print("=== Testing Application Startup Logs ===")

try:
    # Import config and logger modules
    from keeprollming.config import CONFIG, DEFAULT_CTX_LEN, UPSTREAM_BASE_URL
    from keeprollming.logger import log

    configured_routes = CONFIG.get("routes", {})
    route_models = {
        route_name: settings.get("model")
        for route_name, settings in configured_routes.items()
        if isinstance(settings, dict) and settings.get("model")
    }

    print("Simulating startup logging...")

    # This mimics what happens in main() function when the app starts up
    log(
        "INFO",
        "startup",
        upstream=UPSTREAM_BASE_URL,
        log_mode="DEBUG",
        routes=route_models,
        default_ctx=DEFAULT_CTX_LEN,
    )

    print("Startup logs displayed successfully!")

except Exception as e:
    print(f"Error during test: {e}")
    import traceback

    traceback.print_exc()

print("=== Startup log test complete ===")
