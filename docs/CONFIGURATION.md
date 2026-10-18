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

Filter maps compose by filter name across an `extends` chain. With multiple
parents, later parents override earlier parents only for filter names they
define; the child then overrides those same names. Other inherited filters
remain configured. To disable an inherited filter, set that filter's
`enabled: false` explicitly rather than using an empty `filters` map.

`capabilities` is an optional list of static, operator-defined features for a
route, such as `chat`, `streaming`, `tools`, `vision`, or `reasoning`. It is
inherited through `extends`; an explicit empty list clears inherited values.
KRM does not infer capabilities from the upstream.

Route settings include `api_key`, `upstream_headers`, `summary_model`,
`request_timeout`, `fallback_chain`, reasoning-content settings, and `overrides`
for upstream inference parameters. `cost_priority` is accepted for configuration
compatibility but does not affect current fallback or balancing decisions; do
not use it as a routing policy. The full example shows each supported field.

### Route fallback chains

`fallback_chain` is an ordered list of route names. KRM resolves each named
route independently, so a fallback uses its own upstream URL, model,
authentication headers, and request timeout. An omitted value inherits the
parent chain; an explicit empty list (`fallback_chain: []`) clears it. Direct
model-name entries remain supported for existing configurations, but retain the
primary route's transport settings.

For streaming requests, KRM may retry only before any upstream/client-visible
content has been received. Once a stream has committed output, retrying would
duplicate or reorder client content, so the original error is returned instead.

An ordinary upstream HTTP `400` is considered a client-request error, not an
unhealthy route: KRM returns the provider's OpenAI-compatible error envelope
unchanged and does not try the fallback chain. A narrow exception recognizes
backend CUDA/device failures mislabeled as `400`, such as
`cudaMemGetInfo: unspecified launch failure`: these count as upstream failures
and may use the configured fallback chain. This does not make validation,
invalid-parameter or context-overflow `400` errors retryable.
In streaming mode a terminal client-error envelope is
sent as the terminal SSE error event followed by `[DONE]`; in non-streaming
mode its original HTTP status and body are preserved. A `404` model/endpoint
failure remains eligible for the normal pre-output fallback policy. When a
later fallback succeeds, the earlier upstream error is retained in structured
observability and raw capture for diagnosis, but is not exposed to the client.

### Smart routing with `balance`

A route can select one of several independently configured, non-private routes
at request time. `balance` is available on any route name; `public/code` here
is only an example. This is a runtime **reroute**, not inheritance: after a
target is selected, its own upstream URL, model, headers, filters, timeout,
admission policy, and fallback settings apply exactly as for a direct request.

```yaml
routes:
  code/runner-a:
    extends: base/local
    upstream_url: "http://gpu-a.example:8000"
    max_concurrent: 1

  code/runner-b:
    extends: base/local
    upstream_url: "http://gpu-b.example:8000"
    max_concurrent: 1

  public/code:
    extends: base/local
    overrides:
      reasoning_effort: high
    balance:
      # Optional hard contract; false is the default.
      enforce_capabilities: false
      targets:
        - route: code/runner-a
          priority: 100
        - route: code/runner-b
          priority: 80
      # Source values for named overrides win over the selected target.
      # All other execution settings remain owned by that target route.
      preserve_overrides: [reasoning_effort]
      # Carry all configured source filters through the balance reroute.
      # Use a list such as [tool_rewrite] to carry only selected filters.
      preserve_filters: all
      # At request boundaries, permit early promotion when observed PP/decode
      # rates justify the cold-prefill cost. Default: true.
      promote_when_beneficial: true
      # Long-lived affinity remembers a session's likely warm backend. A
      # session reservation keeps the selected idle resource for this session
      # while an agent may issue its next tool-call turn.
      affinity_ttl_seconds: 3600
      idle_reservation:
        enabled: true
        mode: session
        timeout_seconds: 900
        # Optional guards: skip reservations from short or cold turns.
        min_prompt_tokens: 4096
        min_cached_prompt_tokens: 2048
      # If every otherwise-compatible target is busy, retain the downstream
      # request and re-evaluate the whole pool within the route's
      # queue_timeout budget instead of returning an immediate 503.
      selection_retry_interval_ms: 250
      # Once that budget is exhausted, an idle reservation is soft: KRM may
      # use the least-protective reserved target rather than reject it.
      # Physical capacity, health and compatibility are never bypassed.
      allow_reservation_bypass: true
    fallback_chain: ["chat/remote"]
```

#### Capability and context eligibility

For a balanced route, KRM always removes targets that cannot fit the effective
context request before applying affinity, capacity, and target priority.
Capability declarations become an additional hard requirement only with
`balance.enforce_capabilities: true` (the default is `false`, so incomplete
operator metadata cannot unexpectedly make a pool unavailable). When enabled,
KRM uses the target route's static `capabilities` declaration:

- a non-empty `tools` request requires `tools`;
- `image_url`, `input_image`, or `image` content parts require `vision`;
- an enabled `reasoning_effort`, `enable_thinking`, or `thinking` control
  requires `reasoning`.

KRM also compares a conservative prompt estimate, image allowance, requested
completion budget, and safety margin with each target's `ctx_len`. A target
that cannot fit that estimate is skipped in favour of a compatible one. The
post-filter completion clamp remains the final safety boundary because exact
token accounting depends on the backend tokenizer and chat template. Plain
chat does not require an additional capability.

`execution.routing.balance_decision` records requested capabilities, whether
they were enforced, token estimates, and non-sensitive exclusion reasons such as
`missing_capability:vision` or `context_insufficient` per candidate. If every
target is excluded, `execution.routing.balance_rejected` records the same
diagnostic facts before KRM returns its 503 response.

Targets are sorted by descending `priority`, then configuration order. KRM
keeps a bounded, process-local affinity assignment so later turns preferentially
reuse the same eligible target; an upstream-reported positive cache hit is
recorded as additional warmness evidence. The registry retains only HMAC-based
opaque keys, never conversation text or client identifiers. Assignments expire
after one hour by default (configurable with `affinity_ttl_seconds`); a process
restart clears them.

`idle_reservation` is a separate lease created only after a successful balanced
request. It has two explicit modes:

- `priority` (the default) protects an otherwise idle backend only from a
  strictly lower `routing_priority`. Equal-priority sessions may share it and a
  higher-priority session may use it at the next request boundary.
- `session` protects the resource from every other session until expiry. Use it
  for interactive agent routes where keeping a warm prefix between tool-call
  turns matters more than filling every idle slot.

KRM never interrupts an active stream. Set `timeout_seconds` for a fixed lease
(for example `900` for 15 minutes). Without it, duration is interpolated from
`min_seconds` to `max_seconds` using `upstream_prompt_tokens / ctx_len`, bounded
by `min_context_ratio` and `max_context_ratio`; defaults are 60 seconds at 10%
context and 15 minutes at 75% context. By default,
`balance.promote_when_beneficial: true` also lets KRM consider a higher-ranked
eligible target before the lease expires, but only at the next request boundary.
It compares measured prefill/decode rates (at least three complete samples per
route) and estimates total time using the current prompt estimate and requested
completion-token budget. The candidate pays a cold-prefill cost; the current
route receives credit only for upstream-reported cached tokens. No backend
tokenizer or shared-cache assumption is required. If metrics or the completion
budget are missing, or the estimated gain does not exceed the migration cost,
the session stays pinned. Set the flag to `false` for the previous
lease-expiry-only behavior. After KRM acquires capacity on the new target, it
updates affinity and releases the old idle reservation.

Set `min_prompt_tokens` to avoid reserving a resource after short auxiliary
turns such as title generation or an approval review. Set
`min_cached_prompt_tokens` to reserve only when the successful upstream
response positively reports that many reused prompt tokens. Both guards default
to `0` for backward-compatible behaviour. When a positive cache threshold is
configured but the upstream omits cache accounting, KRM deliberately skips the
reservation rather than assuming the backend is warm. The reservation event
records the non-sensitive skip state (`below_minimum_prompt`,
`below_minimum_cached_prompt`, or `cached_usage_unavailable`).

`balance.targets` may contain route names or `{route, priority}` mappings.
`balance.preserve_overrides` is an optional list drawn from the canonical
route-override vocabulary: `temperature`, `top_p`, `top_k`, `max_tokens`,
`frequency_penalty`, `presence_penalty`, `stop`, `seed`, `min_p`,
`repetition_penalty`, and `reasoning_effort`. It is deliberately explicit:
only named source values are carried into a target; the independently resolved
target remains authoritative for every other property.
`balance.preserve_filters` accepts a list of filter-module names, `all`, or
`true`. A list carries only the named modules; `all`/`true` carries every
filter configured on the resolved source route, including filters added there
in the future. Omitted or `false` means none. A named module must be configured
on the source route. Source configuration takes precedence for a filter present
on both routes; target-only filters remain unchanged. The resulting filter map
is normalized again, so duplicate effective priorities are still rejected
during validation.
Targets must exist and be non-private; private routes are inheritance templates
and cannot be execution destinations. Nested balancing selectors are allowed,
but loops are rejected when configuration is loaded. An explicit empty
`targets: []` clears an inherited balancing policy.

KRM tries the selected target, then other balance targets (including a target's
own fallback chain) before it tries the selector route's `fallback_chain`.
As with ordinary fallback, a retry is only safe before client-visible streaming
output begins. The private `GET /routes` endpoint reports selector activity,
target choices, and aggregate affinity evidence for dashboard use.

For the complete decision model — requested route versus selected route,
backend-resource sharing, affinity, reservations, bounded waiting, admission
priority, aging, and first-token pressure — see
[Routing, Balancing & Concurrency](ROUTING_AND_CONCURRENCY.md).

### Per-route admission control

Every resolved route defaults to one concurrent logical request. Set a positive
`max_concurrent` to override that limit; the slot is held for a complete
streaming response, including any time spent waiting for the client to consume
it. `queue_timeout` is expressed in seconds: omit it or set it to `0` to reject
excess requests immediately with `429`; a positive value permits bounded
waiting before the same rejection.

Routes resolving to the same normalized `upstream_url` and model share one
capacity gate and one circuit breaker. `routing_priority` is an optional
integer service-class priority for that shared gate; higher values are served
first **among queued requests**, while equal values remain FIFO. It is distinct
from `balance.targets[].priority`, which only ranks alternatives inside one
selector. No request that is already streaming is interrupted or moved.
For a balancing selector, backend capacity is taken from the selected concrete
target, while `queue_timeout` and `routing_priority` come from the
client-facing selector route.

`queue_aging` is optional starvation protection for a bounded queue. When it
is present, a waiting request receives `priority_step` extra effective priority
for each `interval_seconds` elapsed, up to `max_boost`. It is evaluated only
when a slot becomes free: aging never interrupts an active request, moves a
stream, or lets a new arrival bypass existing queued work. Omit the setting to
keep strict priority/FIFO behavior. The mapping is inherited through
`extends`, so a service route can tune its own queue behavior while sharing a
backend resource with other routes.

`deadline_policy` is an independent, server-owned first-token scheduling hint.
It never reads a client header or timeout, never rejects work on its own, and
never preempts an active stream. When enabled, KRM uses a conservative recent
TTFT estimate for the selected backend only after `min_samples` valid samples
exist. A feasible request whose estimated first token is close to its configured
budget receives up to `max_priority_boost` at the next shared-slot release.
Unknown or insufficient performance history produces no boost.

```yaml
routes:
  code/interactive:
    queue_timeout: 60          # hard, server-owned admission limit
    deadline_policy:
      enabled: true
      first_token_budget_ms: 8000
      urgency_window_ms: 5000
      max_priority_boost: 20
      ttft_safety_factor: 1.5
      min_samples: 5
```

`queue_timeout` remains the hard limit for receiving a backend slot. The
deadline policy only orders competing queued work; it is neutral for infeasible
targets and data-poor routes.

```yaml
routes:
  code/public:
    model: qwen
    upstream_url: "http://gpu-a.example:8000"
    max_concurrent: 2
    queue_timeout: 5

  code/priority:
    model: qwen
    upstream_url: "http://gpu-a.example:8000"
    max_concurrent: 2  # resource-owned settings must match code/public
    queue_timeout: 10  # client waiting tolerance may differ
    routing_priority: 100
    queue_aging:
      interval_seconds: 30
      priority_step: 2
      max_boost: 20
```

The private `GET /routes` endpoint exposes the configured admission settings,
currently queued requests, and active requests for dashboard use.

### Circuit breaker

Set `circuit_breaker_enabled: true` on a route to stop sending requests to an
upstream that is persistently failing. `failure_threshold` is the number of
terminal upstream failures before the circuit opens; `recovery_timeout` is the
number of seconds before one half-open probe is permitted. A successful probe
closes and resets the breaker; a failed probe reopens it. While open KRM
returns `503` with `code: circuit_open`, unless the route has a fallback chain,
in which case it immediately uses the first configured fallback route.

### Privacy-preserving observability

`observability.privacy_mode` defaults to `false`. When enabled, KRM still
executes requests unchanged but prevents model/user content from reaching its
observability persistence boundaries: PLAIN and JSON projections omit
transcripts, reasoning and tool payloads; raw traces retain timing, direction
and byte length but not SSE bytes; request and error captures retain only safe
metadata. Route, model, status, usage and latency remain available for
operations. Existing files are not retroactively altered.

```yaml
observability:
  privacy_mode: true
```

This intentionally disables replay-grade raw captures for new requests. Keep
the default only where the diagnostic value and retention controls are
appropriate for the content being processed.

## Client API keys

`api_key` (singular) remains the bearer credential KRM sends **to an
upstream**. `api_keys` (plural) is the separate list of credentials KRM accepts
**from clients**. Clients use the standard OpenAI header:

```http
Authorization: Bearer your-client-key
```

`api_keys` controls credentials independently from network location. Declare it
at the root to require those keys on every route by default. The setting is
inherited through `extends`; a route-level list replaces the inherited list,
and an explicit empty list clears inherited keys. Use `client_access` below to
combine key authentication with LAN restrictions.

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

## Client network access policies

Use `client_access` to restrict routes by the caller's network without forcing
LAN clients to configure API keys. The existing default is `any` for
configuration compatibility; a deployment that wants a LAN/private and
internet/public split should set the root default to `lan`, then mark only
internet-facing routes as `public` and give them effective `api_keys`:

```yaml
client_access:
  default: lan
  lan_networks:
    - "192.168.8.0/24"   # replace with the actual client LAN
    - "127.0.0.0/8"
    - "::1/128"
  trusted_proxies:
    - "192.168.8.199/32" # optional; exact proxy address as seen by KRM

routes:
  chat/main:
    # Inherits LAN-only access; no client key required.
    model: "local-model"
    upstream_url: "http://127.0.0.1:8080"

  public/chat:
    extends: chat/main
    client_access: public
    api_keys: ["replace-with-a-public-client-key"]
```

`client_access` supports `lan`, `public`, and `any` on routes. `lan` requires
the resolved client IP to match one of `lan_networks`; `public` permits any
source but requires at least one effective API key; `any` disables the source
restriction and is intended only for deliberate legacy/open deployments.
Network and API-key checks compose: a route configured with `lan` and keys
requires both. These policies are checked against the requested route before
balancing, fallback, or rerouting, so a destination cannot weaken the caller's
source-route policy. `/v1/models` omits routes inaccessible to the caller.

KRM trusts `X-Forwarded-For` only when the direct TCP peer matches
`trusted_proxies`. It walks the chain from the peer inward and uses the first
untrusted address. Forwarded headers from untrusted peers are ignored, and a
trusted proxy without a valid forwarded chain is denied for LAN-only routes.
Do not add broad proxy CIDRs: use the exact proxy address or smallest
operator-controlled subnet. `GET /routes` and `GET /metrics` use the root
default network policy; `/health` remains an unauthenticated liveness check.

## Private route status endpoint

`GET /routes` provides a dashboard-oriented snapshot of every public route.
It is intentionally private: deploy it only on a trusted network or place it
behind your own reverse-proxy authentication. For a direct route it returns the
resolved upstream URL/model, static `capabilities`, context and completion
limits. A balancing selector instead has `model_mode: "balanced"` and no
misleading single upstream; its `balance_policy.resolved_targets` lists the
independently resolved target routes with their endpoint/model, declared
capabilities, limits, current admission snapshot, and circuit-breaker state.
It also reports a rolling 60-minute window of request activity, errors, and
average request metrics. `execution.routing.balance_rejected` outcomes appear
as selector errors when every target is ineligible. The endpoint also lists
in-flight `pending_requests` (preparation or upstream connection) and
`active_requests` (an executing non-stream request or a connected stream),
including request id, phase and elapsed time.

The endpoint is event-fed and entirely in memory. On startup it seeds the same
one-hour window from recent performance JSONL records, so completed requests
remain visible across a restart; requests that were still in flight at restart
cannot be reconstructed. Error messages are included for private operational
debugging, but are capped to avoid an unbounded response.

## Filters

In streaming mode, a tool/reasoning loop stopper's exhausted fallback is
evaluated by Model Nudge before closing the response. If its text matches a
nudge trigger and continuation budget remains, KRM appends the continuation
to that fallback. Rejected tool calls do not suppress this check. The loop
stopper's retry budget is not reset; Nudge consumes its own budget, subject
to the shared recovery limit. Non-matching fallbacks close normally.

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

`multimodal_validator.max_images` optionally bounds the number of image parts
sent upstream. When it is exceeded, `max_images_policy: strip_first` (the
default) replaces the oldest excess images and retains the newest ones.
Set `max_images_policy: strip_latest` to retain the oldest images instead.
Stripped parts remain visible to the model as
`max_images_replacement_text`, preserving the surrounding message structure.

To reject unsupported file attachments without discarding actual images:

```yaml
filters:
  multimodal_validator:
    enabled: true
    strip_all_files: true
    files_replacement_text: "[File omitted: attachments are disabled]"
    strip_all_images: false
```

This sanitizes structured file/PDF parts throughout the submitted history, not
only the last message. The default is `strip_all_files: false`; PDF pages
already rendered as real images are not files to this switch. See
[Filter pipeline](FILTER_PIPELINE.md) for recognized formats and the RLS/TLS
detection/retry/message contract, including their independent retry budgets.

## Observability

Restart KRM after changing projector, privacy-mode or capture configuration:
the corresponding consumers are created at startup. Route hot reload does not
rebuild these consumers. In particular, switching a capture policy back to
`disabled` requires a restart to actually stop that capture.

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

### Request snapshots versus raw SSE traces

These are separate diagnostics. The optional root-level request capture is:

```yaml
request_capture:
  policy: selected_routes  # disabled by default; also accepts all
  selected_routes: ["chat/local"]
```

It writes `__request_captures__/<UTC-date>/<req_id>/request.raw_inbound.json`
plus metadata. Despite that filename, the captured `raw_body` is the effective
upstream payload **after route resolution/repacking but before request
filters**, not the original inbound client JSON or exact final HTTP body.
System prompt injection and other request filters can still change it.
`selected_routes` matches concrete **resolved** route names; for a balanced
selector, list its backend routes rather than only the client-facing selector.

Error-body capture is configured separately at the root with
`body_capture: {policy: errors_only}` (the default); use `policy: disabled` to
turn it off. It writes bounded request/error bodies to `__body_captures__/`
when an upstream/request failure is observed, including upstream failures
recovered by fallback. This is not a general successful-response capture.

`observability.raw_trace` instead stores response-stream bytes with boundaries
and monotonic timing. It does not capture the outgoing HTTP request body.
Neither facility alone guarantees an exact replay of the complete original
request. Use targeted capture, protect the files as sensitive, and restore
`policy: disabled` when diagnosis is complete. `privacy_mode: true` suppresses
message content in these projections rather than providing a replayable body.
For live filtering by route, backend, request ID or event, see the
[log viewer examples](TROUBLESHOOTING.md#follow-logs-for-an-original-route-or-resolved-backend).

When a route declares `fallback_chain`, KRM emits two related operational
events. `execution.chat.fallback_chain` records the resolved, non-secret
attempt plan (position, route, model, endpoint and timeout). Each actual
transition emits `execution.chat.fallback`, identifying the failed and next
route/model/endpoint, attempt position, and cause (`http_status`, `timeout`,
`transport_error`, or `circuit_open`). PLAIN renders these as a compact
`FROM`/`TO` block; JSON and raw traces retain the structured fields. Headers
and API keys are never included.

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
supported keys are `temperature`, `top_p`, `top_k`, `max_tokens`,
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
