# Routing Rules System - Design Sketch

## Overview
Transform the current profile-based routing into a flexible **route-based configuration system** where each route can define:
- Model pattern matching (prefix-based)
- Whether to summarize or passthrough
- Backend model settings (main_model, summary_model, ctx_len, etc.)
- Profile-specific behaviors (transform_reasoning_content, add_empty_content_when_reasoning_only, etc.)

## Configuration Structure

```yaml
# routes: list of routing rules
routes:
  # Route 1: Default profile-based route
  - name: quick-route
    pattern: "local/quick"           # Model prefix to match
    summary_enabled: true            # Enable summarization (default: true)
    passthrough_enabled: false       # Disable passthrough (default: false)
    
    # Profile settings for this route
    main_model: qwen2.5-3b-instruct
    summary_model: qwen2.5-1.5b-instruct
    ctx_len: 8192
    max_tokens: 2048
    
    # Reasoning content handling
    transform_reasoning_content: false
    add_empty_content_when_reasoning_only: false
    reasoning_placeholder_content: "..."

  # Route 2: Passthrough route (like current pass/*)
  - name: passthrough-route
    pattern: "pass/"                 # Matches any model starting with "pass/"
    summary_enabled: false           # Disable summarization
    passthrough_enabled: true        # Enable passthrough
    
    # Backend can be specified or extracted from pattern
    backend_model_pattern: "${1}"    # Capture group from pattern (e.g., pass/(.*) -> $1)

  # Route 3: Custom route with specific settings
  - name: deep-route
    pattern: "local/deep"
    summary_enabled: true
    passthrough_enabled: false
    
    main_model: qwen2.5-27b-instruct
    summary_model: qwen2.5-7b-instruct
    ctx_len: 16384
    max_tokens: 4096

# Default fallback route (if no pattern matches)
default_route:
  pattern: "*"
  main_model: qwen2.5-v1-7b-instruct
  summary_model: qwen2.5-3b-instruct
  ctx_len: 8192
```

## Key Features

### 1. Pattern Matching (Prefix-based)
- Routes are matched in order of definition
- First matching pattern wins
- Special patterns:
  - `pass/*` → matches any model starting with "pass/"
  - `local/*` → matches models prefixed with "local/"
  - `*` → wildcard fallback route

### 2. Route Actions
Each route can define:
- **summary_enabled**: Whether to apply summarization logic
- **passthrough_enabled**: Whether to bypass orchestrator and forward directly
- **backend_model**: The actual upstream model to use (can be extracted from pattern)

### 3. Profile Settings Per Route
Instead of global profiles, each route can have its own complete configuration:
- `main_model`: Primary model for this route
- `summary_model`: Model used for summarization
- `ctx_len`: Context window size
- `max_tokens`: Maximum completion tokens
- Reasoning content handling options

### 4. Backward Compatibility
- Existing `model_aliases` can still be used as shortcuts
- `pass/*` pattern maintains current passthrough behavior
- Default route ensures existing configs continue to work

## Implementation Plan

### Phase 1: Core Infrastructure
1. Create `Route` dataclass with all configuration fields
2. Update config loader to parse routes from YAML/JSON
3. Implement prefix-based matching logic
4. Add fallback/default route handling

### Phase 2: Integration with App
1. Replace `resolve_profile_and_models()` with new routing resolver
2. Update app.py to use route settings for decision making
3. Ensure passthrough and summary logic respect route config

### Phase 3: Migration & Testing
1. Add migration guide from old profile-based config
2. Write tests for various routing scenarios
3. Verify backward compatibility with existing configs

## Example Configurations

### Simple Migration (from current config)
```yaml
# Old style (still supported via default_route)
profiles:
  quick: { main_model: ..., summary_model: ... }

# New style
routes:
  - name: quick
    pattern: "local/quick"
    main_model: qwen2.5-3b-instruct
    summary_model: qwen2.5-1.5b-instruct
```

### Advanced Multi-backend Setup
```yaml
routes:
  # Route to local model with summarization
  - name: local-summary
    pattern: "local/summary"
    main_model: local/qwen3.5-35b-a3b
    summary_model: local/qwen2.5-7b-instruct
    ctx_len: 16384
    
  # Route to external API without summarization
  - name: external-api
    pattern: "api/openai"
    passthrough_enabled: true
    backend_model_pattern: "${1}"
    
  # Default fallback
  - name: default
    pattern: "*"
    main_model: qwen2.5-v1-7b-instruct
    summary_model: qwen2.5-3b-instruct
```

## Benefits
1. **Flexibility**: Each route can have completely independent settings
2. **Clarity**: Routing logic is explicit in configuration, not hidden in code
3. **Extensibility**: Easy to add new routing behaviors without code changes
4. **Backward Compatible**: Existing configs continue to work via default_route
