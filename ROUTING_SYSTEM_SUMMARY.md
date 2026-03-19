# Route-Based Configuration System - Implementation Summary

## Overview

Successfully refactored the orchestrator from a profile-based routing system to a flexible **route-based configuration system** with built-in default routes and automatic fallback chain support.

## Completed Phases

### ✅ Phase 1: Design & Planning
- Created comprehensive design document (`_docs/design/Routing-Rules-System.md`)
- Defined feature specifications for route matching, pattern support, and fallback chains
- Established built-in default routes (quick, main, deep, code/senior, code/junior, pass/*)

### ✅ Phase 2: Core Infrastructure
**Files Created/Modified:**
- `keeprollming/routing.py` - Core routing logic with Route dataclass
- `tests/test_routing.py` - 25 unit tests for routing functionality
- `keeprollming/config.py` - Updated config loader to parse routes from YAML

**Key Features Implemented:**
- Route dataclass with full configuration support (main_model, summary_model, ctx_len, max_tokens, reasoning handling, fallback_chain, circuit_breaker)
- Built-in default routes defined programmatically
- Prefix-based pattern matching with wildcard (`pass/*`) and multiple patterns (`local/quick|quick`) support
- Fallback chain resolution with loop prevention via visited model tracking

### ✅ Phase 3: Integration with App
**Files Modified:**
- `keeprollming/app.py` - Integrated routing system into request handling

**Key Changes:**
- Replaced `resolve_profile_and_models()` with new `resolve_route()`, `get_route_settings()`, and `resolve_fallback_chain()` functions
- Updated all imports to use routing module
- Implemented fallback chain logic for both streaming and non-streaming requests:
  - Tracks visited models to prevent infinite loops
  - Automatically retries with next model in chain on backend failure
  - Logs each fallback attempt for debugging
  - Returns detailed error if all options exhausted

### ✅ Phase 4: Cleanup & Migration
**Files Created/Modified:**
- `config.yaml` - Updated to use new routes format (removed profiles, model_aliases)
- `keeprollming/config.py` - Removed environment variable overrides, legacy profile support deprecated
- `MIGRATION_TO_ROUTES.md` - Comprehensive migration guide

**Key Changes:**
- All model configuration now goes through routes section in YAML
- Environment variables for models removed (MAIN_MODEL, SUMMARY_MODEL, etc.)
- Old profile system deprecated but kept for backward compatibility during transition
- Model aliases replaced with route patterns

### ✅ Phase 5: Testing & Validation
**Test Results:**
- **71 tests passing** (25 new routing tests + 46 existing tests)
- No regressions in existing functionality
- All orchestrator tests pass
- Config loading tests verify new format works correctly

## Built-in Default Routes

| Route Name | Pattern | Main Model | Summary Model | Context Length | Use Case |
|------------|---------|------------|---------------|----------------|----------|
| `quick` | `local/quick\|quick` | qwen2.5-3b-instruct | qwen2.5-1.5b-instruct | 8192 | Fast responses, low latency |
| `main` | `local/main\|main` | qwen2.5-v1-7b-instruct | qwen2.5-3b-instruct | 8192 | Balanced performance (default) |
| `deep` | `local/deep\|deep` | qwen2.5-27b-instruct | qwen2.5-7b-instruct | 16384 | Maximum quality, long context |
| `code/senior` | `code/senior\|senior` | qwen3.5-35b-a3b | qwen2.5-7b-instruct | 16384 | Complex coding tasks |
| `code/junior` | `code/junior\|junior` | qwen2.5-7b-instruct | qwen2.5-1.5b-instruct | 8192 | Simple coding tasks |
| `pass/*` | `pass/\*` | (extracted from pattern) | N/A | N/A | Passthrough to any backend |

## Configuration Example

```yaml
routes:
  quick:
    pattern: "local/quick|quick"
    main_model: "unsloth/qwen3.5-35b-a3b"
    summary_model: "unsloth/qwen3.5-35b-a3b"
    ctx_len: 4096
    max_tokens: 2048
    
  code/senior:
    pattern: "code/senior|senior"
    main_model: "qwen3.5-35b-a3b"
    summary_model: "qwen2.5-7b-instruct"
    ctx_len: 16384
    fallback_chain:
      - "qwen2.5-27b-instruct"
      - "local/main|main"

upstream_base_url: http://arkai.local:1234/v1
```

## Key Features

### 1. Pattern Matching
- **Exact match**: `pattern: "my-model"`
- **Multiple patterns (OR)**: `pattern: "model-a|model-b|model-c"`
- **Wildcard with capture groups**: `pattern: "pass/*"`, `backend_model_pattern: "${1}"`

### 2. Fallback Chains
Automatic rerouting when backend fails:
```yaml
fallback_chain:
  - "qwen2.5-27b-instruct"  # Try deep model first
  - "local/main|main"        # Then try main profile  
  - "local/quick|quick"      # Finally quick profile
```

### 3. Circuit Breaker Support (Optional)
```yaml
circuit_breaker_enabled: true
failure_threshold: 3
recovery_timeout: 60
```

## Migration from Old Config

**Before:**
```yaml
profiles:
  quick: { main_model: "qwen2.5-3b", summary_model: "qwen2.5-1.5b" }
model_aliases: { fast: "quick" }
main_model: qwen2.5-v1-7b
```

**After:**
```yaml
routes:
  quick:
    pattern: "local/quick|quick"
    main_model: "qwen2.5-3b-instruct"
    summary_model: "qwen2.5-1.5b-instruct"
```

## Testing Commands

```bash
# Run all unit tests (no e2e)
pytest tests/ --ignore=tests/e2e -v

# Run routing-specific tests
pytest tests/test_routing.py -v

# Verify config loading
python -c "from keeprollming.config import USER_ROUTES; print([r.name for r in USER_ROUTES])"
```

## Files Modified/Created

### Created:
- `keeprollming/routing.py` - Core routing logic (337 lines)
- `tests/test_routing.py` - 25 unit tests
- `MIGRATION_TO_ROUTES.md` - Migration guide
- `_docs/design/Routing-Rules-System.md` - Design document

### Modified:
- `keeprollming/app.py` - Integrated routing system (100+ lines changed)
- `keeprollming/config.py` - Updated config loader, removed env vars
- `config.yaml` - New routes-based format

## Backward Compatibility

The old profile system is **deprecated but still functional** during transition:
- `resolve_profile_and_models()` still works (delegates to new routing)
- Legacy `profiles:` and `model_aliases:` sections ignored in favor of `routes:`
- Environment variables for models no longer override YAML config

## Next Steps

1. ✅ All phases completed successfully
2. ✅ 71 tests passing with no regressions
3. ⚠️ Consider removing legacy profile support in future release (after migration period)
4. 📝 Update README.md with new configuration examples
5. 🔧 Add circuit breaker metrics to monitoring system

---

**Status**: All phases completed successfully. System ready for production use.
