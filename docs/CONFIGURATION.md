# Configuration

## File Location

Default: `config.yaml` in the project root. Override with `CONFIG_FILE` env var.

## Structure

```yaml
models:
  model-name: {ctx_len: 65536}

routes:
  route-name:
    extends: base-route        # optional: inherit from another route
    pattern: "model-pattern"   # glob pattern for model selection
    model: "upstream-model"    # model name sent to upstream
    upstream_url: "..."        # upstream LLM endpoint
    filter_chain:
      order: [filter1, filter2, ...]
      filters:
        filter_name:
          enabled: true
          ...
```

## Route Inheritance

Routes can extend other routes via `extends`:

```yaml
routes:
  base/deep:
    model: qwen3.5-4b
    ctx_len: 131072
    filter_chain:
      order: [system_prompt, model_nudge]
      filters:
        model_nudge: {enabled: true, trigger_patterns: [":$"]}

  code/review:
    extends: base/deep
    pattern: "code/*"
    # inherits model, ctx_len, filter_chain from base/deep
```

Priority: route-level > inherited > root defaults.

## Filter Configuration

Each filter has its own config keys. Common ones:

| Filter | Key | Default | Description |
|--------|-----|---------|-------------|
| SystemPrompt | `prompt` | — | System prompt text |
| SystemPrompt | `override` | `false` | Override instead of prepend |
| ModelNudge | `trigger_patterns` | `[":$"]` | Regex patterns for lazy detection |
| ModelNudge | `nudge_message` | `"Continue."` | Message sent on retry |
| ModelNudge | `max_nudge_attempts` | `3` | Max retries |
| ToolLoopStopper | `max_repeats` | `3` | Identical calls before intervention |
| ToolRewrite | (none) | — | Enabled/disabled only |

## Env Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_FILE` | `config.yaml` | Config path |
| `UPSTREAM_BASE_URL` | — | Upstream base URL (fallback if route lacks it) |
| `LOG_MODE` | `json` | `BASIC_PLAIN` for human-readable |
