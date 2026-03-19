# Migration Guide: Old Profile-Based Config to New Route System

## Overview

The orchestrator has been refactored from a profile-based routing system to a flexible **route-based configuration system**. This guide helps you migrate your existing `config.yaml` to the new format.

**Key Changes:**
- ✅ All model configuration now goes through routes (no more environment variables)
- ✅ Built-in default routes work out-of-the-box (quick, main, deep, code/senior, code/junior, pass/*)
- ✅ Automatic fallback chains for backend rerouting when models fail
- ✅ Pattern matching with wildcards (`pass/*`, `code/*`) and multiple patterns (`local/quick|quick`)

## What's Removed

The following legacy configuration options are **no longer supported**:

1. **Environment Variables** - All model settings must be in config.yaml:
   - ❌ `MAIN_MODEL` → Use routes instead
   - ❌ `SUMMARY_MODEL` → Use route's summary_model field
   - ❌ `PASSTHROUGH_PREFIX` → Use `pass/*` pattern

2. **Old Profile System** - Profiles section is deprecated:
   ```yaml
   # OLD (deprecated):
   profiles:
     myprofile:
       main_model: "qwen2.5-7b"
       summary_model: "qwen2.5-3b"
   ```

3. **Model Aliases** - Use route patterns instead:
   ```yaml
   # OLD (deprecated):
   model_aliases:
     fast: "local/quick"
   ```

## Migration Examples

### Example 1: Simple Model Configuration

**Before:**
```yaml
# Old config.yaml
main_model: qwen2.5-7b-instruct
summary_model: qwen2.5-3b-instruct
model_aliases:
  fast: qwen2.5-3b-instruct
  slow: qwen2.5-27b-instruct
```

**After:**
```yaml
# New config.yaml - routes section
routes:
  # Quick profile (fast responses)
  quick:
    pattern: "local/quick|quick"
    main_model: "qwen2.5-3b-instruct"
    summary_model: "qwen2.5-1.5b-instruct"
    
  # Main profile (balanced performance)  
  main:
    pattern: "local/main|main"
    main_model: "qwen2.5-v1-7b-instruct"
    summary_model: "qwen2.5-3b-instruct"
    
  # Deep profile (maximum quality)
  deep:
    pattern: "local/deep|deep"
    main_model: "qwen2.5-27b-instruct"
    summary_model: "qwen2.5-7b-instruct"
```

### Example 2: Code-Specific Routing

**Before:**
```yaml
# Old config.yaml - no code-specific routing
main_model: qwen3.5-35b-a3b
summary_model: qwen2.5-7b-instruct
model_aliases:
  senior: "qwen3.5-35b-a3b"
  junior: "qwen2.5-7b-instruct"
```

**After:**
```yaml
# New config.yaml - code-specific routes with fallback chains
routes:
  # Senior developer mode (best model for complex coding)
  code/senior:
    pattern: "code/senior|senior"
    main_model: "qwen3.5-35b-a3b"
    summary_model: "qwen2.5-7b-instruct"
    ctx_len: 16384
    fallback_chain:
      - "qwen2.5-27b-instruct"  # Try deep model first if senior fails
      - "local/main|main"        # Then try main profile
      
  # Junior developer mode (simpler, faster)
  code/junior:
    pattern: "code/junior|junior"
    main_model: "qwen2.5-7b-instruct"
    summary_model: "qwen2.5-1.5b-instruct"
    ctx_len: 8192
```

### Example 3: Passthrough Configuration

**Before:**
```yaml
# Old config.yaml - passthrough via environment variable
PASSTHROUGH_PREFIX: "pass/"
main_model: qwen2.5-v1-7b-instruct
summary_model: qwen2.5-3b-instruct
```

**After:**
```yaml
# New config.yaml - built-in pass/* pattern (no configuration needed!)
# The pass/* route is automatically available without any config
# Just use it directly in your requests:
# POST /v1/chat/completions with model: "pass/openai/gpt-4"
```

### Example 4: Custom Fallback Chains

**Before:**
```yaml
# Old config.yaml - no automatic fallback support
main_model: qwen2.5-v1-7b-instruct
summary_model: qwen2.5-3b-instruct
```

**After:**
```yaml
# New config.yaml - automatic fallback on backend failure
routes:
  production:
    pattern: "prod/*"
    main_model: "qwen3.5-35b-a3b"
    summary_model: "qwen2.5-7b-instruct"
    
    # Automatic rerouting if primary model fails
    fallback_chain:
      - "qwen2.5-27b-instruct"  # Try deep model first
      - "local/main|main"        # Then try main profile  
      - "local/quick|quick"      # Finally quick profile
      
    circuit_breaker_enabled: true
    failure_threshold: 3
    recovery_timeout: 60
```

## Built-in Default Routes (No Config Needed)

The following routes are **automatically available** without any configuration:

| Route Name | Pattern | Main Model | Summary Model | Context Length | Use Case |
|------------|---------|------------|---------------|----------------|----------|
| `quick` | `local/quick\|quick` | qwen2.5-3b-instruct | qwen2.5-1.5b-instruct | 8192 | Fast responses, low latency |
| `main` | `local/main\|main` | qwen2.5-v1-7b-instruct | qwen2.5-3b-instruct | 8192 | Balanced performance |
| `deep` | `local/deep\|deep` | qwen2.5-27b-instruct | qwen2.5-7b-instruct | 16384 | Maximum quality, long context |
| `code/senior` | `code/senior\|senior` | qwen3.5-35b-a3b | qwen2.5-7b-instruct | 16384 | Complex coding tasks |
| `code/junior` | `code/junior\|junior` | qwen2.5-7b-instruct | qwen2.5-1.5b-instruct | 8192 | Simple coding tasks |
| `pass/*` | `pass/\*` | (extracted from pattern) | N/A | N/A | Passthrough to any backend |

## Migration Checklist

- [ ] Remove all environment variables for model configuration
- [ ] Replace `profiles:` section with `routes:` section
- [ ] Update `model_aliases:` to use route patterns instead
- [ ] Add fallback chains for critical routes (optional but recommended)
- [ ] Test each route pattern works as expected
- [ ] Verify passthrough (`pass/*`) routing still functions

## Testing Your Migration

1. **Test built-in routes:**
   ```bash
   curl -X POST http://localhost:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
       "model": "local/quick",
       "messages": [{"role": "user", "content": "Hello"}]
     }'
   ```

2. **Test custom routes:**
   ```bash
   curl -X POST http://localhost:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
       "model": "code/senior",
       "messages": [{"role": "user", "content": "Write Python code"}]
     }'
   ```

3. **Test passthrough:**
   ```bash
   curl -X POST http://localhost:8000/v1/chat/completions \
     -H "Content-Type: application/json" \
     -d '{
       "model": "pass/openai/gpt-4",
       "messages": [{"role": "user", "content": "Hello"}]
     }'
   ```

## Common Patterns

### Pattern Matching Examples

```yaml
routes:
  # Exact match
  exact:
    pattern: "my-exact-model"
    
  # Multiple patterns (OR logic)
  multiple:
    pattern: "model-a|model-b|model-c"
    
  # Wildcard with capture groups
  wildcard:
    pattern: "backend/\*"
    backend_model_pattern: "${1}"
    
  # Prefix matching
  prefix:
    pattern: "company/models/*"
```

### Fallback Chain Formats

```yaml
routes:
  myroute:
    pattern: "my/route"
    main_model: "primary-model"
    
    # Simple model names
    fallback_chain:
      - "secondary-model"
      - "tertiary-model"
      
    # Route references (built-in or user-defined)
    fallback_chain:
      - "local/quick|quick"  # Reference built-in route
      
    # Complex options with conditions (future feature)
    fallback_chain:
      - model: "fallback-model"
        condition: "always"  # or specific error codes
```

## Need Help?

- Check `_docs/design/Routing-Rules-System.md` for detailed specifications
- Review `config.example.yaml` for complete configuration examples
- Run unit tests: `pytest tests/test_routing.py -v`

---

**Note:** During the transition period, old profile-based configs will still work but are deprecated. Plan to migrate at your earliest convenience.
