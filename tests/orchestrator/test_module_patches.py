"""Fixes for tests to work with modular app.py structure.

This file contains monkeypatch helper functions that apply patches at all
necessary locations when tests mock functions from app_mod.

The key insight: When the original monolithic app.py imported these functions,
patching app_mod.func() would propagate to all code. But now that code is in
modular files that import directly from their source modules, we need to patch
at multiple locations.

Usage in tests:
    from tests.conftest import patch_app_mod_functions

    def test_something(monkeypatch):
        async def mock_func(*args, **kwargs):
            return "mock"

        patch_app_mod_functions(monkeypatch, {
            'summarize_middle': mock_func,
            'should_summarise': mock_should_summarise,
        })
"""

def patch_app_mod_functions(monkeypatch, func_patches: dict):
    """Apply a function patch at all necessary import locations.

    Args:
        monkeypatch: pytest monkeypatch fixture
        func_patches: dict mapping function names to mock implementations
    """
    import keeprollming.summary as summary_mod
    import keeprollming.summary as summary_mod
    from keeprollming.endpoints import chat_completions
    from keeprollming.processing import summarization as processing_summarization_mod
    import keeprollming.summary_cache as summary_cache_mod
    import keeprollming.metrics as metrics_mod

    # Also patch at import locations for modular code to work
    # This is critical: chat_completions.py imports from summary directly
    for name, func in func_patches.items():
        if hasattr(summary_mod, name):
            monkeypatch.setattr(summary_mod, name, func)
        if hasattr(chat_completions, name):
            monkeypatch.setattr(chat_completions, name, func)
        if hasattr(processing_summarization_mod, name):
            monkeypatch.setattr(processing_summarization_mod, name, func)
        if hasattr(summary_cache_mod, name):
            monkeypatch.setattr(summary_cache_mod, name, func)
        if hasattr(metrics_mod, name):
            monkeypatch.setattr(metrics_mod, name, func)

    # Also patch where it's used in chat_completions.process_chat_request
    # The function is imported at module level and called directly
    if 'should_summarise' in func_patches:
        monkeypatch.setattr('keeprollming.endpoints.chat_completions.should_summarise', func_patches['should_summarise'])
