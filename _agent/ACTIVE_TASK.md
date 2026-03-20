# Active Task: Config Validator & Health Check Tool

**Status:** `in_progress`  
**Created:** 19/03/2026 14:30  
**Task ID:** config-validator-health-check

---

## User Request

Create a comprehensive configuration validation and health check tool for the orchestrator that:

1. **Config Validation Mode**: Validates configuration files for structural correctness and completeness
   - Ensures all routes have required fields (either directly defined or inherited)
   - Validates inheritance chains are valid and don't create circular references
   - Checks that non-private routes can be fully resolved with all settings
   - Reports any missing upstream URLs, model names, or other required fields

2. **E2E Health Check Mode**: Performs live testing of configured routes
   - Makes actual API calls to backends for each route
   - Verifies responses are valid and come from expected models
   - Reports health status of each route/backend pair
   - Provides summary of system-wide health

3. **Use Cases**:
   - Initial setup validation before deploying new configs
   - Periodic health checks during operation
   - Debugging tool for routing issues

---

## Goal

Build a CLI tool (`validate_config.py` or similar) that can:
- Validate config structure and inheritance chains
- Test live connectivity to backends
- Provide clear, actionable error messages
- Support both offline validation and online health checks

---

## Why This Matters

Currently there's no way to:
- Verify a config file is correct before starting the server
- Diagnose why a route isn't working as expected
- Monitor the health of configured backends over time

This tool will enable safer configuration management and easier troubleshooting.

---

## Clarifications

- **"Non-private" routes**: Routes that are not marked with `is_private: true` should be fully functional
- **Private routes**: Can be used for hierarchical organization without needing all settings
- **Validation scope**: Check that inheritance chains resolve completely with all required fields
- **Health check scope**: Test actual connectivity and response from backends

---

## Likely Files

- `keeprollming/validator.py` - New file for validation logic
- `keeprollming/healthcheck.py` - New file for E2E health checks  
- `validate_config.py` - CLI entry point (or add subcommands to existing script)
- Tests in `tests/test_validator.py` and `tests/test_healthcheck.py`

---

## Constraints

- Must work with existing config format (no breaking changes)
- Should be non-intrusive (doesn't modify running server state)
- Health checks should have configurable timeouts to avoid hanging
- Error messages should be clear and actionable for users

---

## Proposed Approach

### Phase 1: Config Validation (`keeprollming/validator.py`)

1. Load config file and parse routes
2. Build inheritance graph from all routes
3. Detect circular references in inheritance chains
4. For each non-private route:
   - Resolve full inheritance chain
   - Verify all required fields are present (upstream_url, main_model, etc.)
   - Check for missing parent routes in extends references
5. Report validation errors with line numbers and suggestions

### Phase 2: E2E Health Check (`keeprollming/healthcheck.py`)

1. Load config and resolve all non-private routes
2. For each route:
   - Extract upstream_url from resolved settings
   - Make test request to backend (e.g., `/v1/chat/completions` with minimal payload)
   - Verify response contains expected model name
   - Measure latency and report status
3. Aggregate results into health report

### Phase 3: CLI Tool (`validate_config.py`)

```bash
# Validation only
python validate_config.py --config config.yaml validate

# Health check (live testing)
python validate_config.py --config config.yaml healthcheck

# Both validation + health check
python validate_config.py --config config.yaml --full-check

# Verbose output
python validate_config.py --config config.yaml --verbose
```

---

## Test Plan

### Unit Tests
- Test inheritance chain resolution with various scenarios
- Test circular reference detection
- Test validation of missing required fields
- Test health check timeout handling

### Integration Tests
- Validate against existing config.yaml (should pass)
- Create intentionally broken configs and verify validation catches them
- Test health check against real backends (LM Studio, Lemonade)

### Manual Testing
- Run `validate_config.py --config broken_config.yaml validate` with invalid configs
- Run `validate_config.py --config config.yaml healthcheck` and verify output
- Test with different timeout values

---

## Done When

✅ `keeprollming/validator.py` validates config structure and inheritance  
✅ `keeprollming/healthcheck.py` performs E2E backend testing  
✅ CLI tool `validate_config.py` provides both modes  
✅ Tests cover validation logic and edge cases  
✅ Documentation in README.md for usage  
✅ Clear error messages with actionable suggestions  

---

## Out of Scope

- Modifying existing config format
- Auto-fixing invalid configurations
- Real-time monitoring (this is a one-time check tool)
- Integration with external monitoring systems (Prometheus, etc.)

---

## Notes for Agent

1. Start with Phase 1 - validation logic is simpler and doesn't require running backends
2. Use existing routing.py functions (`resolve_inherited_route`, `get_route_settings`) to avoid duplication
3. For health checks, use a minimal payload to reduce latency
4. Consider adding `--dry-run` flag for health checks that shows what would be tested without actually testing
5. Make sure to handle network errors gracefully with proper timeouts

---

**Completion Timestamp:** TBD (when all criteria are met)
