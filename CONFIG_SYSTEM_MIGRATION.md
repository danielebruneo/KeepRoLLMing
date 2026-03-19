# Configuration System Migration Notes

## Overview
The configuration system has been migrated from environment variables and inline model definitions to a centralized `config.yaml` file with proper route management.

## Key Changes

### 1. Private Route Markers (`@private` → `is_private: true`)

**Before:**
```yaml
routes:
  "@private"  # Marker key (quoted due to YAML syntax)
  arkai/lmstudio:
    upstream_base_url: "http://arkai.local:1234/v1"
```

**After:**
```yaml
routes:
  arkai/lmstudio:
    is_private: true  # Explicit boolean flag
    upstream_base_url: "http://arkai.local:1234/v1"
```

**Benefits:**
- Cleaner YAML syntax (no quoted strings needed)
- More explicit and self-documenting
- Type-safe (boolean vs string)
- Easier to extend with additional route metadata

### 2. Configuration File Structure

All settings are now centralized in `config.yaml`:

```yaml
# Root-level defaults (applied when not overridden)
ctx_len: 8192
max_tokens: 4096
upstream_base_url: "http://arkai.local"
summary_enabled: true
passthrough_enabled: false

# Route configurations with 3-level hierarchy
routes:
  # Backend definitions (private routes)
  arkai/lmstudio:
    is_private: true
    upstream_base_url: "http://arkai.local:1234/v1"
  
  # Model-specific routes
  arkai/GPU1/QWen3.5-35b-a3b:
    is_private: true
    extends: arkai/lmstudio
    main_model: "qwen3.5-35b-a3b@q3_k_s"
    ctx_len: 128000
    max_tokens: 32768
  
  # Public routes (not marked as private)
  chat/quick:
    extends: [chat, base/quick]
```

### 3. Route Inheritance Hierarchy

The system now supports a clean 3-level configuration hierarchy:

1. **Route level** - Highest priority (e.g., `ctx_len` on specific route)
2. **Model-specific** - Medium priority (settings from model config)
3. **Root-level defaults** - Lowest priority (global defaults)

### 4. Environment Variables

The `run.sh` script now only sets runtime overrides:

```bash
# Runtime-only settings (quick tweaks)
export DEFAULT_CTX_LEN=8000
export SUMMARY_MAX_TOKENS=1000
export SAFETY_MARGIN_TOK=1000

# All other settings live in config.yaml
CONFIG_FILE="./config.yaml"
```

### 5. Code Changes

#### `keeprollming/config.py`
- Removed `"@private"` decorator handling code (first pass to delete keys)
- Added `is_private = route_data.get("is_private", False)` extraction
- Pass `_is_private=is_private` to Route constructor
- Updated `get_private_routes()` to use `USER_ROUTES` instead of scanning config dict

#### `keeprollming.py`
- Removed imports for deprecated environment variable constants (`BASE_MAIN_MODEL`, `QUICK_MAIN_MODEL`, etc.)
- Simplified startup logging to show route count instead of individual model names

## Migration Guide

### For Existing Configurations

If you have an old `config.yaml` with `"@private"` markers:

1. Remove the standalone `"@private"` keys
2. Add `is_private: true` to each route that should be private
3. Keep all other settings unchanged

Example migration:
```yaml
# Old format
routes:
  "@private"
  arkai/lmstudio:
    upstream_base_url: "http://arkai.local:1234/v1"

# New format  
routes:
  arkai/lmstudio:
    is_private: true
    upstream_base_url: "http://arkai.local:1234/v1"
```

### For New Configurations

Start with the template in `config.example.yaml` or use the current `config.yaml` as reference.

## Testing

All 57 core tests pass with the new configuration system:
- `test_routing.py` - Route matching and fallback chains
- `test_config.py` - Configuration loading and parsing
- `test_3level_config.py` - Inheritance hierarchy
- `test_models_endpoint.py` - `/v1/models` endpoint (excludes private routes)
- `test_custom_prompts.py` - Custom prompt handling

## Backward Compatibility

The old environment variable approach is **no longer supported** for model definitions. All models and routes must be defined in `config.yaml`.

Runtime overrides via environment variables still work for:
- `DEFAULT_CTX_LEN`
- `SUMMARY_MAX_TOKENS`
- `SAFETY_MARGIN_TOK`
- `LOG_LEVEL` / `LOG_MODE`
- `HOST`, `PORT`
