"""Startup regression tests for BLOCKER-FIX-STARTUP-REGRESSION-001.

These tests verify that the lifespan context manager does not raise
AttributeError due to name collision between the events_app module import
and the lifespan parameter.

Regression: AttributeError: 'FastAPI' object has no attribute 'emit_perf_logs_dir'
Root cause: lifespan parameter `_app` shadowed `from .observability import events_app as _app`
Fix: Renamed lifespan parameter from `_app` to `app`.
"""

import asyncio
import pytest
from fastapi import FastAPI


class TestLifespanStartupNoAttributeError:
    """Verify lifespan startup completes without AttributeError."""

    def test_lifespan_smoke_no_attribute_error(self):
        """Run the lifespan context manager and assert no AttributeError.

        This test actually executes the startup code path without mocking
        the emit calls, ensuring the fix is real and not just a mock bypass.
        """
        from keeprollming.app import lifespan

        app = FastAPI(lifespan=lifespan)

        async def run_lifespan():
            # Enter lifespan (startup)
            async with app.router.lifespan_context(app):
                pass  # Just verify startup completes

        asyncio.run(run_lifespan())
        # If we reach here without AttributeError, the fix is working.

    def test_events_app_module_import_not_shadowed(self):
        """Verify that events_app module functions are callable after import.

        This confirms the _app module-level import in app.py is not shadowed
        by any local binding when accessed outside the lifespan function.
        """
        from keeprollming.observability import events_app as _app

        # These should be callable without error (they're functions on the module)
        assert hasattr(_app, "emit_perf_logs_dir")
        assert hasattr(_app, "emit_config_reloaded")
        assert hasattr(_app, "emit_starting")
        assert hasattr(_app, "emit_stopping")

        # Actually call them to verify they work (no dispatcher = fallback to log)
        _app.emit_perf_logs_dir(message="test")
        _app.emit_config_reloaded(message="test")
        _app.emit_starting(message="test")
        _app.emit_stopping(message="test")


class TestLifespanParameterRename:
    """Verify the lifespan function signature uses 'app' parameter, not '_app'."""

    def test_lifespan_signature_uses_app_parameter(self):
        """Confirm lifespan parameter is named 'app', not '_app'.

        This is a structural check that the fix was applied correctly.
        """
        import inspect
        from keeprollming.app import lifespan

        sig = inspect.signature(lifespan)
        params = list(sig.parameters.keys())

        assert "app" in params, f"Expected 'app' parameter, got: {params}"
        assert "_app" not in params, f"'_app' parameter still present: {params}"
