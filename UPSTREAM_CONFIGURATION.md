# Per-Route Upstream Configuration

This document describes how to configure different upstream servers for different routes in KeepRolling.

## Overview

The routing system now supports **per-route upstream configuration**, allowing you to route traffic to multiple backend servers based on the requested model pattern. This is useful for:

- Load balancing across multiple backends
- A/B testing with different models/services
- Cost optimization by routing to cheaper backends when appropriate
- Geographic distribution of requests
- Fallback redundancy at the upstream level (in addition to model fallback chains)

## Configuration Syntax

### Basic Upstream URL per Route

```yaml
routes:
  quick-route:
    pattern: "quick/*"
    main_model: "qwen2.5-3b"
    upstream_url: "https://fast-backend.example.com/v1"
    
  premium-route:
    pattern: "premium/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://premium-backend.example.com/v1"
```

### Upstream with Custom Headers

```yaml
routes:
  secure-route:
    pattern: "secure/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://api.secure-backend.com/v1"
    upstream_headers:
      X-API-Key: "your-api-key-here"
      X-Custom-Auth: "bearer-token"
      X-Tenant-ID: "tenant-123"
```

### Mixed Configuration (Some Routes with Upstream, Some Without)

Routes without `upstream_url` will use the global `UPSTREAM_BASE_URL` from your configuration.

```yaml
routes:
  # Uses custom upstream
  fast-route:
    pattern: "fast/*"
    main_model: "qwen2.5-3b"
    upstream_url: "https://fast.example.com/v1"
    
  # Uses global UPSTREAM_BASE_URL (default behavior)
  standard-route:
    pattern: "standard/*"
    main_model: "qwen2.5-v1-7b"
    
  # Another custom upstream with headers
  enterprise-route:
    pattern: "enterprise/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://enterprise.example.com/v1"
    upstream_headers:
      X-API-Key: "${ENTERPRISE_API_KEY}"
```

## Complete Example Configuration

```yaml
# Global settings
upstream_base_url: "https://default-backend.example.com/v1"

routes:
  # Quick responses for simple queries - use fast, cheap backend
  quick:
    pattern: "quick/*|local/quick"
    main_model: "qwen2.5-3b"
    ctx_len: 8192
    max_tokens: 2048
    upstream_url: "https://fast-backend.example.com/v1"
    
  # Main model for general use - default backend
  main:
    pattern: "main/*|local/main"
    main_model: "qwen3.5-35b-a3b"
    ctx_len: 32768
    max_tokens: 8192
    
  # Deep reasoning for complex tasks - premium backend
  deep:
    pattern: "deep/*|local/deep"
    main_model: "qwen3.5-35b-a3b"
    ctx_len: 131072
    max_tokens: 16384
    upstream_url: "https://premium-backend.example.com/v1"
    
  # Code generation - specialized code backend
  code/senior:
    pattern: "code/*|local/code"
    main_model: "qwen3.5-35b-a3b"
    ctx_len: 65536
    max_tokens: 8192
    upstream_url: "https://code-backend.example.com/v1"
    upstream_headers:
      X-Code-Mode: "true"
      
  # Junior code assistance - cost-effective backend
  code/junior:
    pattern: "junior/*"
    main_model: "qwen2.5-7b"
    ctx_len: 16384
    max_tokens: 4096
    upstream_url: "https://junior-code-backend.example.com/v1"
    
  # Passthrough - no transformation, uses specified backend
  pass/*:
    pattern: "pass/*"
    passthrough_enabled: true
    
  # Fallback chain example with different upstreams
  resilient-route:
    pattern: "resilient/*"
    main_model: "model-a"
    fallback_chain:
      - model-b  # Same upstream, different model
      - name: model-c
        upstream_url: https://backup.example.com/v1  # Different upstream
```

## How It Works

### Request Flow with Per-Route Upstream

1. **Client request** arrives with a model name (e.g., `quick/simple-query`)
2. **Pattern matching** finds the best matching route (`quick/*` matches)
3. **Upstream resolution**:
   - If route has `upstream_url`, use it
   - Otherwise, fall back to global `UPSTREAM_BASE_URL`
4. **Headers applied**: Route-specific headers are merged with request
5. **Request forwarded** to the resolved upstream URL

### Header Precedence

- Route-level `upstream_headers` take precedence over any global header configuration
- Headers can use environment variable substitution: `"${ENV_VAR}"`

## Use Cases

### 1. Cost Optimization

Route different model families to backends with different pricing:

```yaml
routes:
  cheap-route:
    pattern: "cheap/*"
    main_model: "qwen2.5-3b"
    upstream_url: "https://low-cost-backend.example.com/v1"
    
  expensive-route:
    pattern: "expensive/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://high-performance-backend.example.com/v1"
```

### 2. A/B Testing

Test a new backend against the current one:

```yaml
routes:
  ab-test-group-a:
    pattern: "ab/a/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://current-backend.example.com/v1"
    
  ab-test-group-b:
    pattern: "ab/b/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://new-backend-beta.example.com/v1"
```

### 3. Geographic Distribution

Route based on user location or model naming:

```yaml
routes:
  eu-route:
    pattern: "eu/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://eu-backend.example.com/v1"
    
  us-route:
    pattern: "us/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://us-backend.example.com/v1"
```

### 4. Blue-Green Deployment

```yaml
routes:
  blue-deployment:
    pattern: "blue/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://blue-backend.example.com/v1"
    
  green-deployment:
    pattern: "green/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "https://green-backend.example.com/v1"
```

## Environment Variable Substitution

You can use environment variables in `upstream_headers` values:

```yaml
routes:
  secure-route:
    pattern: "secure/*"
    main_model: "qwen3.5-35b-a3b"
    upstream_url: "${UPSTREAM_URL}"
    upstream_headers:
      X-API-Key: "${API_KEY}"
      X-Tenant-ID: "${TENANT_ID:-default}"  # Default value if not set
```

## Migration from Global Upstream

If you're currently using a global `upstream_base_url` and want to add per-route upstreams:

1. Keep the global `UPSTREAM_BASE_URL` for routes that don't need custom backends
2. Add `upstream_url` only to routes that require different backends
3. Test each route individually to ensure correct routing

## Troubleshooting

### Route not using expected upstream

- Check that `upstream_url` is correctly specified in the route configuration
- Verify the pattern matches your requested model name
- Check logs for "upstream_req_repacked" to see which URL was used

### Headers not being sent

- Ensure headers are defined as a dictionary under `upstream_headers`
- Check that environment variables are properly set if using substitution
- Review logs for header configuration errors

## API Reference

### Route Configuration Fields

| Field | Type | Description |
|-------|------|-------------|
| `pattern` | string | Model pattern to match (e.g., `"quick/*"`) |
| `upstream_url` | string | Optional custom upstream URL for this route |
| `upstream_headers` | dict | Optional custom headers to send with requests |

### Route Object Fields (Python)

```python
@dataclass(frozen=True)
class Route:
    name: str
    pattern: str
    # ... other fields ...
    upstream_url: Optional[str] = None  # Custom upstream URL
    upstream_headers: Dict[str, str] = field(default_factory=dict)  # Custom headers
```

### get_route_settings() Return Value

Returns a dictionary including:

```python
{
    "route_name": "quick",
    "backend_model": "qwen2.5-3b",
    "upstream_url": "https://custom.example.com/v1",  # Route-specific or None
    "upstream_headers": {"X-Custom": "value"},  # Route-specific headers (may be empty)
    # ... other settings ...
}
```

## See Also

- [MIGRATION_TO_ROUTES.md](./MIGRATION_TO_ROUTES.md) - Migration guide from profile-based routing
- [ROUTING_SYSTEM_SUMMARY.md](./ROUTING_SYSTEM_SUMMARY.md) - Complete routing system overview
- [_docs/design/Routing-Rules-System.md](_docs/design/Routing-Rules-System.md) - Design documentation
