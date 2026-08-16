# Configuration

KeepRoLLMing reads `config.yaml` from the project root. Set `CONFIG_FILE` to
use another file. The canonical examples are
[`config.example.yaml`](../config.example.yaml) and
[`config.example.full.yaml`](../config.example.full.yaml).

## Root settings

`ctx_len`, `max_tokens`, `summary_enabled`, `passthrough_enabled`, and
`default_request_timeout` provide defaults for routes. Route values override
them. `default_max_completion_tokens` controls the fallback completion limit
when a client does not supply one. `performance_logs_dir` chooses where
per-request JSONL records and the dashboard snapshot are stored.

```yaml
ctx_len: 32768
max_tokens: 4096
default_request_timeout: 120
performance_logs_dir: "__performance_logs"
```

## Routes and inheritance

Clients select routes through the request `model` field. A route can inherit
backend settings from one or more base routes with `extends`.

```yaml
routes:
  base/local:
    is_private: true
    model: "qwen-model"
    upstream_url: "http://127.0.0.1:8080"

  chat/local:
    extends: base/local
    pattern: "chat/local"
    max_tokens: 2048
```

`is_private: true` is useful for base routes: they remain usable as inheritance
parents but are not advertised by `GET /v1/models`.

Route settings include `api_key`, `upstream_headers`, `summary_model`,
`request_timeout`, `fallback_chain`, `cost_priority`, reasoning-content
settings, and `overrides` for upstream inference parameters. The full example
shows each supported field.

## Filters

Filters live directly under `routes.<name>.filters`:

```yaml
filters:
  system_prompt:
    enabled: true
    prompt_file: "prompts/code-assistant.md"
    override: false
  model_nudge:
    enabled: true
    trigger_patterns: [":$"]
    max_attempts: 3
```

There is no `filter_chain`, `order`, or nested `filters` wrapper. YAML order is
not semantic. Built-in modules define their default request/stream priority;
`priority: <integer>` is the deliberate per-route override. Equal effective
priorities for enabled modules are rejected at configuration load time.

The available module names are `system_prompt`, `summarization`,
`multimodal_validator`, `tool_rewrite`, `timestamp`, `model_nudge`,
`model_tool_loop_stopper`, and `reasoning_loop_stopper`. Their accepted keys
are demonstrated in the full example and validated by the module registry.

`system_prompt` accepts either a short inline `prompt` or a UTF-8
`prompt_file`; they are mutually exclusive. Relative paths are resolved from
the directory containing `config.yaml` (or `CONFIG_FILE`), so they remain
stable when the server is launched from elsewhere. Prompt files are read and
validated at startup/reload: a missing, unreadable, or invalid file prevents
that configuration from being applied. This works identically for every
route, including `code/architect` and `code/executor`.

## Observability

PLAIN, JSON, and server logs are independent projections of runtime events.
They can be configured independently and rotate using `max_bytes` and
`backup_count`. Raw SSE capture is separate and should normally remain off:

```yaml
observability:
  projectors:
    plain:
      level: BASIC
      stdout: true
  raw_trace:
    policy: disabled  # disabled | all | selected_routes
```

Use `selected_routes` for short, targeted raw captures. Raw traces contain
transport bytes and can include sensitive content.

## Performance dashboard

Every completed request is appended immediately to a route-specific JSONL file
in `performance_logs_dir`. The terminal dashboard reads `summary.yaml`, which
is regenerated after the first completed request and then every configured
number of requests:

```yaml
performance:
  summary_update_interval: 20
```

The setting is read at server startup, so restart KRM after changing it. The
dashboard's `--interval` option controls only how often it rereads the already
written `summary.yaml`; it does not cause the server to regenerate that file.

`prompt_tokens` remains the logical client-visible prompt size. When the
upstream reports `usage.prompt_tokens_details.cached_tokens`, performance
records also expose `cached_prompt_tokens` and `uncached_prompt_tokens`.
`prompt_tps` and `total_tps` use the uncached prompt portion, so they describe
actual upstream prefill and end-to-end work rather than cache-inflated logical
throughput.

## Route request overrides

`routes.<name>.overrides` forces selected inference parameters on every
upstream request for that route, replacing a client-provided value. The
supported keys are `temperature`, `top_p`, `max_tokens`,
`frequency_penalty`, `presence_penalty`, `stop`, `seed`, `min_p`,
`repetition_penalty`, and `reasoning_effort`.

```yaml
routes:
  chat/deep:
    overrides:
      reasoning_effort: high
```

`reasoning_effort` is sent as a top-level OpenAI-compatible field, matching
LibreChat's reasoning-effort control. The upstream model/runtime determines
which values it accepts; KeepRoLLMing forwards the configured value unchanged.
