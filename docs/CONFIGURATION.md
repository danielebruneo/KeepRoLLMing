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

## Request-body safety limit

KRM rejects oversized JSON bodies before decoding them, including requests
received with chunked transfer encoding. This is a process-wide OOM guard, not
a route policy: route selection happens only after the body is parsed. The
default is intentionally broad (64 MiB), so ordinary long conversations and
normal multimodal requests are unaffected. Override it only if a deployment
genuinely needs larger payloads:

```yaml
request_limits:
  max_body_bytes: 67108864
```

Requests above the limit receive HTTP `413` with
`error.code: request_too_large`. An invalid `request_limits` value logs a
warning and safely falls back to the default; it does not make a live reload
all-or-nothing.

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

`capabilities` is an optional list of static, operator-defined features for a
route, such as `chat`, `streaming`, `tools`, `vision`, or `reasoning`. It is
inherited through `extends`; an explicit empty list clears inherited values.
KRM does not infer capabilities from the upstream.

Route settings include `api_key`, `upstream_headers`, `summary_model`,
`request_timeout`, `fallback_chain`, `cost_priority`, reasoning-content
settings, and `overrides` for upstream inference parameters. The full example
shows each supported field.

## Client API keys

`api_key` (singular) remains the bearer credential KRM sends **to an
upstream**. `api_keys` (plural) is the separate list of credentials KRM accepts
**from clients**. Clients use the standard OpenAI header:

```http
Authorization: Bearer your-client-key
```

Declare `api_keys` at the root to protect every route by default. The setting
is inherited through `extends`; a route-level list replaces the inherited list,
and an explicit empty list makes that route public.

```yaml
api_keys: ["replace-with-a-client-secret"]

routes:
  base/admin:
    is_private: true
    api_keys: ["admin-only-secret"]

  chat/admin:
    extends: base/admin
    pattern: "chat/admin"

  public/demo:
    pattern: "public/demo"
    api_keys: []
```

Authentication is enforced before filters, request capture, or upstream work.
The caller credential is never forwarded upstream and is redacted from KRM’s
diagnostic header events. `POST /v1/chat/completions` and
`POST /v1/embeddings` return `401` for a missing or invalid key. `GET /v1/models`
returns only public routes accessible with the key presented by that caller.

## Private route status endpoint

`GET /routes` provides a dashboard-oriented snapshot of every public route.
It is intentionally private: deploy it only on a trusted network or place it
behind your own reverse-proxy authentication. It returns the resolved upstream
URL/model, static `capabilities`, context and completion limits, and a rolling
60-minute window of request activity, errors, and average request metrics. It
also lists in-flight `pending_requests` (preparation or upstream connection)
and `active_requests` (an executing non-stream request or a connected stream),
including request id, phase and elapsed time.

The endpoint is event-fed and entirely in memory. On startup it seeds the same
one-hour window from recent performance JSONL records, so completed requests
remain visible across a restart; requests that were still in flight at restart
cannot be reconstructed. Error messages are included for private operational
debugging, but are capped to avoid an unbounded response.

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
  # Optional PLAIN/JSON/server projections run through this bounded FIFO.
  # Overflow drops only projection output and emits a WARN diagnostic event.
  projector_queue_size: 2048
  projectors:
    plain:
      level: BASIC
      stdout: true
  raw_trace:
    policy: disabled  # disabled | all | selected_routes
```

Use `selected_routes` for short, targeted raw captures. Raw traces contain
transport bytes and can include sensitive content.

PLAIN, JSON and compact server projections never synchronously write on a
streaming request. They each retain event order through a bounded FIFO worker.
`projector_queue_size` defaults to 2048. If a projection cannot keep up, KRM
keeps serving requests, drops only that optional projection's pending events,
and emits `execution.observability.projection_overflow` with the projector
name and dropped-event count. Increase the queue only after investigating the
sink, disk, or terminal that is slow; it is not a substitute for healthy I/O.

## Upstream transport pool

KRM owns one shared asynchronous HTTP connection pool for upstream requests.
Routes retain their own request_timeout; the pool policy is process-wide and is
configured once at startup:

~~~yaml
upstream_transport:
  max_connections: 100
  max_keepalive_connections: 20
  keepalive_expiry: 30
  pool_timeout: 10
  connect_timeout: 60
~~~

pool_timeout bounds how long a request may wait for an available upstream
connection; it is independent from a route request deadline. Startup emits the
effective policy as execution.upstream.transport_configured, making it possible
to distinguish a configured pool limit from backend latency during incident
triage. Restart KRM after changing these settings.

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
