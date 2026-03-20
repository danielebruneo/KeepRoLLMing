# Handoff Notes - Route-Based Configuration System

## Current Status (March 19, 2026)

### What's Been Completed
✅ Design document created at `_docs/design/Routing-Rules-System.md`  
✅ Route dataclass defined in `config.py` with fallback_chain support  
✅ Built-in default routes implemented programmatically  
✅ Prefix-based pattern matching with wildcard support (pass/*, code/*)  
✅ Unit tests for routing logic written  

### Current Blocker
Test failure in `tests/test_orchestrator.py`:

**Problem:** The test expects summarization to be triggered but it's not happening.

**Root Cause:** Test fixture mock returns `ctx_len=512` which is too small:
- Threshold calculation: `max(256, ctx_eff - max_out - safety_margin)`
- With ctx_len=512 and max_tokens=1024 → threshold becomes negative/zero
- `should_summarise()` returns False even with long prompts

**Test Details:**
```python
@pytest.fixture
def mock_model_info():
    return {
        "local/main": {"ctx_len": 512, "max_tokens": 1024},
        # ... other models also have ctx_len=512
    }
```

### Recommended Next Steps

1. **Fix the test fixture** - Increase `ctx_len` to a realistic value (e.g., 8192 or 16384) so summarization can trigger:
   ```python
   "local/main": {"ctx_len": 8192, "max_tokens": 4096},
   ```

2. **Verify fallback chain routing** - Test that the fallback_chain feature works correctly when primary backend is unavailable

3. **Complete integration tests** - Ensure all route types work:
   - Built-in routes: quick, main, deep
   - Code-specific: code/senior, code/junior  
   - Passthrough: pass/*

4. **Run full test suite** - `pytest` to verify no regressions

### Files to Review
- `_docs/design/Routing-Rules-System.md` - Design document
- `config.py` - Route dataclass and built-in routes
- `tests/test_routing.py` - Unit tests for routing logic
- `tests/test_orchestrator.py` - Integration test with failing assertion

### Key Decisions Made
- Built-in default routes preserve old profile-based functionality
- Routes use prefix pattern matching (first-match-wins)
- Fallback chain can reference route names OR direct model names
- Loop prevention via visited models tracking per request
