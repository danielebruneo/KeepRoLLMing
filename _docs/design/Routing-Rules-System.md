# Routing Rules System - Design Sketch

## Overview
Transform the current profile-based routing into a flexible **route-based configuration system** where each route can define:
- Model pattern matching (prefix-based)
- Whether to summarize or passthrough
- Backend model settings (main_model, summary_model, ctx_len, etc.)
- Profile-specific behaviors (transform_reasoning_content, add_empty_content_when_reasoning_only, etc.)

## Default Routes (Built-in)
The system includes these default routes that work even without explicit configuration:

```yaml
# Default route 1: Quick profile - fast responses with summarization
routes:
  - name: quick-default
    pattern: "local/quick" | "quick"
    summary_enabled: true
    passthrough_enabled: false
    main_model: qwen2.5-3b-instruct
    summary_model: qwen2.5-1.5b-instruct
    ctx_len: 8192

# Default route 2: Main profile - balanced performance
  - name: main-default
    pattern: "local/main" | "main"
    summary_enabled: true
    passthrough_enabled: false
    main_model: qwen2.5-v1-7b-instruct
    summary_model: qwen2.5-3b-instruct
    ctx_len: 8192

# Default route 3: Deep profile - maximum context and quality
  - name: deep-default
    pattern: "local/deep" | "deep"
    summary_enabled: true
    passthrough_enabled: false
    main_model: qwen2.5-27b-instruct
    summary_model: qwen2.5-7b-instruct
    ctx_len: 16384

# Default route 4: Code/Senior - specialized for senior developer tasks
  - name: code-senior-default
    pattern: "code/senior" | "senior"
    summary_enabled: true
    passthrough_enabled: false
    main_model: qwen3.5-35b-a3b
    summary_model: qwen2.5-7b-instruct
    ctx_len: 16384

# Default route 5: Code/Junior - simplified for junior developer tasks
  - name: code-junior-default
    pattern: "code/junior" | "junior"
    summary_enabled: true
    passthrough_enabled: false
    main_model: qwen2.5-7b-instruct
    summary_model: qwen2.5-1.5b-instruct
    ctx_len: 8192

# Default route 6: Passthrough - bypass summarization, forward directly
  - name: passthrough-default
    pattern: "pass/*"
    summary_enabled: false
    passthrough_enabled: true
    backend_model_pattern: "${1}"  # Extract model from pass/(.*)
```

## Custom Routes (User-Defined)
Users can add additional routes or override defaults in their config.yaml:

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

## Pattern Matching Syntax
Routes support multiple pattern formats:

1. **Exact match**: `"local/quick"` - matches only "local/quick"
2. **Prefix with wildcard**: `"pass/*"` or `"code/*"` - matches any model starting with prefix
3. **Multiple patterns**: Use `|` to combine patterns (e.g., `"local/quick" | "quick"`)
4. **Capture groups**: `${1}` extracts matched portion from pattern for backend_model

## Matching Priority
Routes are evaluated in order:
1. User-defined routes (from config.yaml) - first match wins
2. Built-in default routes - checked if no user route matches
3. Fallback route (`*`) - catches all unmatched models

## Fallback Chain Routing
Each route can define a `fallback_chain` for automatic rerouting when backend is unavailable:

```yaml
routes:
  - name: deep-route
    pattern: "deep"
    main_model: qwen2.5-27b-instruct
    summary_model: qwen2.5-7b-instruct
    
    # Fallback chain: try these models if primary fails
    fallback_chain:
      - "qwen2.5-14b-instruct"  # Try medium model first
      - "qwen2.5-7b-instruct"   # Then smaller model
      - "local/quick"           # Finally use quick profile
    
    # Optional: max chain depth to prevent infinite loops (default: 3)
    max_fallback_depth: 3
```

### Fallback Chain Examples

#### Example 1: Deep to Quick Degradation
```yaml
- name: deep-fallback
  pattern: "deep"
  main_model: qwen3.5-35b-a3b
  fallback_chain:
    - "qwen2.5-27b-instruct"  # Medium model
    - "local/deep"            # Local deep if available
    - "code/senior"           # Senior code route as last resort
```

#### Example 2: Code Routes with Fallbacks
```yaml
- name: code-senior-fallback
  pattern: "code/senior"
  main_model: qwen3.5-35b-a3b
  fallback_chain:
    - "qwen2.5-14b-instruct"
    - "local/main"            # Fall back to main profile
```

### Fallback Chain Resolution Order
When a request comes in with fallback chain:
1. Try primary backend model
2. If unavailable, iterate through fallback_chain in order
3. For each fallback option:
   - Check if it's a route name (e.g., "code/senior") → resolve to its models
   - Or direct model name (e.g., "qwen2.5-14b-instruct")
   - Skip if already visited in current request chain (prevent loops)
   - Attempt routing through that backend
4. Return success on first working option
5. If all options exhausted, return error with full chain details

### Advanced Fallback Features

#### Conditional Fallbacks
Use conditions to determine when to trigger fallback:
```yaml
fallback_chain:
  - model: "qwen2.5-14b-instruct"
    condition: "error_code == 429 or timeout"  # Only on rate limit/timeout
  - model: "local/main"
    condition: "always"  # Always try this if previous fails
```

#### Circuit Breaker Integration
Track failure rates and skip failed models temporarily:
```yaml
fallback_chain:
  - "qwen2.5-14b-instruct"

circuit_breaker:
  enabled: true
  failure_threshold: 3      # Open after 3 failures
  recovery_timeout: 60       # Try again after 60 seconds
```

#### Cost-Aware Fallbacks
Prefer cheaper models in fallback chain:
```yaml
fallback_chain:
  - model: "qwen2.5-14b-instruct"
    cost_priority: 1          # Higher priority = try first
  - model: "local/main"
    cost_priority: 2
```

### Error Handling
If fallback chain is completely exhausted:
```json
{
  "error": "All backend models unavailable",
  "request_model": "deep/qwen3.5-35b-a3b",
  "fallback_attempts": [
    {"model": "qwen2.5-14b-instruct", "status": "unavailable"},
    {"model": "qwen2.5-7b-instruct", "status": "timeout"},
    {"model": "local/quick", "status": "error"}
  ],
  "suggestion": "Check backend availability or configure fallback routes"
}
```

## Implementation Plan

### Phase 1: Core Infrastructure
1. Create `Route` dataclass with all configuration fields (including fallback_chain)
2. Define built-in default routes (quick, main, deep, code/senior, code/junior, pass/*)
3. Update config loader to parse user-defined routes from YAML/JSON
4. Implement prefix-based pattern matching logic with wildcard support
5. Add fallback/default route handling
6. Write unit tests for routing logic

### Phase 2: Integration with App
1. Replace `resolve_profile_and_models()` with new routing resolver
2. Update app.py to use route settings for decision making
3. Ensure passthrough and summary logic respect route config
4. Support capture groups in pattern matching (e.g., pass/(.*) -> $1)
5. Implement fallback chain resolution logic:
   - Track visited models per request to prevent loops
   - Attempt each fallback option sequentially
   - Log each fallback attempt for debugging
   - Return detailed error if all options fail
6. Write integration tests

### Phase 3: Migration & Testing
1. Add migration guide from old profile-based config
2. Write tests for various routing scenarios including code/senior and code/junior
3. Test fallback chain scenarios (deep -> medium -> quick)
4. Verify backward compatibility with existing configs
5. Performance testing (ensure no regression)

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
