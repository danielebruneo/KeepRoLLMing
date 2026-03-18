# Active Task: Route-Based Configuration System

## Overview
Refactor the profile-based routing system into a flexible route-based configuration system where each route can define its own model settings, summarization behavior, and passthrough options.

**Key Design Decision**: Built-in default routes will preserve existing functionality (quick/main/deep profiles + pass/* passthrough) while allowing custom routes like code/senior and code/junior.

## Goals
- Preserve old profile-based routes as built-in defaults: quick, main, deep
- Preserve pass/* passthrough behavior as built-in default route
- Add new built-in routes: code/senior, code/junior
- Support prefix-based pattern matching (like `pass/*`, `code/*`)
- Each route defines: main_model, summary_model, ctx_len, max_tokens, reasoning handling
- Maintain backward compatibility with existing configs
- **NEW**: Implement fallback chain routing for automatic rerouting when backend unavailable

## Default Routes (Built-in)
1. **quick**: Fast responses with summarization (local/quick, quick)
2. **main**: Balanced performance (local/main, main)
3. **deep**: Maximum context and quality (local/deep, deep)
4. **code/senior**: Specialized for senior developer tasks (code/senior, senior)
5. **code/junior**: Simplified for junior developer tasks (code/junior, junior)
6. **pass/***: Passthrough - bypass summarization, forward directly

## Fallback Chain Feature
Each route can define a `fallback_chain` to automatically reroute requests if primary backend is unavailable:

```yaml
routes:
  - name: deep-route
    pattern: "deep"
    main_model: qwen2.5-27b-instruct
    
    # Fallback chain: try these models if primary fails
    fallback_chain:
      - "qwen2.5-14b-instruct"  # Try medium model first
      - "local/quick"           # Finally use quick profile
```

**Key Features:**
- Chain can reference route names (e.g., "code/senior") or direct model names
- Prevents infinite loops by tracking visited models per request
- Logs each fallback attempt for debugging
- Returns detailed error if all options fail

## Tasks

### Phase 1: Design & Planning ✅
- [x] Sketch out feature design
- [x] Create design document (_docs/design/Routing-Rules-System.md)
- [x] Add built-in default routes (quick/main/deep/code/senior/code/junior/pass/*)
- [ ] Review and refine design with stakeholders

### Phase 2: Core Infrastructure
- [ ] Create `Route` dataclass in config.py (include fallback_chain field)
- [ ] Define built-in default routes programmatically
- [ ] Update config loader to parse user-defined routes from YAML/JSON
- [ ] Implement prefix-based pattern matching logic with wildcard support (pass/*, code/*)
- [ ] Add fallback/default route handling
- [ ] Write unit tests for routing logic

### Phase 3: Integration with App
- [ ] Replace `resolve_profile_and_models()` with new routing resolver
- [ ] Update app.py to use route settings for decision making
- [ ] Ensure passthrough and summary logic respect route config
- [ ] Support capture groups in pattern matching (e.g., pass/(.*) -> $1)
- [ ] Implement fallback chain resolution:
  - Track visited models per request to prevent loops
  - Attempt each fallback option sequentially
  - Log each fallback attempt for debugging
  - Return detailed error if all options fail
- [ ] Write integration tests

### Phase 4: Migration & Documentation
- [ ] Add migration guide from old profile-based config
- [ ] Update README with new configuration examples
- [ ] Write comprehensive documentation
- [ ] Create example configs for common use cases (code/senior, code/junior)
- [ ] Document fallback chain usage and best practices

### Phase 5: Testing & Validation
- [ ] Test backward compatibility with existing configs
- [ ] Verify all existing tests still pass
- [ ] Add new e2e tests for routing scenarios including code/senior and code/junior
- [ ] Test fallback chain scenarios (deep -> medium -> quick)
- [ ] Performance testing (ensure no regression)

## Acceptance Criteria
1. ✅ Design document created and reviewed
2. Built-in default routes work without any config: quick, main, deep, code/senior, code/junior, pass/*
3. Routes can be defined in config.yaml with prefix patterns including wildcards (pass/*, code/*)
4. Each route has independent main_model, summary_model, ctx_len settings
5. `summary_enabled` flag controls whether summarization is applied
6. `passthrough_enabled` flag controls passthrough behavior
7. First-match-wins routing logic works correctly with wildcard patterns
8. Default fallback route handles unmatched models
9. **NEW**: Fallback chain routing works correctly:
   - Chain can reference route names or direct model names
   - Prevents infinite loops by tracking visited models per request
   - Logs each fallback attempt for debugging
   - Returns detailed error if all options fail
10. Backward compatible with existing profile-based configs
11. All tests pass (unit + integration + e2e)

## Timeline
- **Phase 1**: Complete ✅
- **Phase 2**: ~4 days (includes wildcard pattern matching, capture groups, and fallback chain infrastructure)
- **Phase 3**: ~3 days (includes fallback chain resolution logic)  
- **Phase 4**: ~1 day
- **Phase 5**: ~2 days (includes extensive fallback testing)
- **Total**: ~10-12 days

## Notes
- Keep existing `model_aliases` as shortcuts for route names during transition
- Support both old profile-based and new route-based configs during transition
- Ensure clear error messages when config is invalid
- Document the order of precedence (user routes > built-in defaults > fallback)
