# Changelog

All notable changes to this project will be documented in this file.

## v0.9.6

### Added
- A public upgrade guide explains 0.9.6's output-budget behavior, routing
  defaults, shared-worker reservations and graceful shutdown changes.
- Backend-owned `allow_idle_reservation` (default `true`) can disable exclusive
  idle leases on a shared multi-request resource without disabling affinity or
  measured promotion. Hot reload clears old leases and blocks stale completion
  callbacks from recreating them. Full shared targets use the real admission
  queue, preserving cross-route service priority rather than racing selector
  polls; `/routes` reports the effective resource policy.
- BASIC PLAIN now exposes selector/admission queue entry and waited exit,
  effective queue priority, and compact backend-promotion decisions without
  per-poll noise. DEBUG transport milestones distinguish connecting, sending
  and waiting for response headers in both serving modes and `/routes` status.
- Promotion's current-turn output forecast is independently configurable via
  `balance.promotion.expected_current_completion_tokens` (default 1024), bounded
  by the available cap without modifying upstream `max_tokens`.
- `GET /models` is a direct alias of `GET /v1/models`, with identical model
  discovery and API-key/client-network filtering.
- Promotion rate evidence now uses recent samples, then stable historical
  samples for the same upstream URL/model, then optional route-level
  `performance_hint` rates. `balance.promotion.metrics` controls sample,
  freshness, variability and conservative gain thresholds. Bounded aggregates
  are event-fed; an atomic shutdown snapshot restores history at startup.
  Error/multi-attempt totals and legacy records without resource identity are
  excluded. Detailed decisions include source, count, age and confidence.
- Promotion estimates now include configurable future prefill and decode gains
  via `balance.promotion`: defaults are 5 additional turns, 8192 new prompt
  tokens and 1024 completion tokens per turn. Cold context migration is charged
  once; `lookahead_turns: 0` restores current-turn-only evaluation. Fixed-size
  arithmetic uses existing in-memory rates, without parsing logs/history.
- Opt-in `balance.selection_policy: least_loaded` assigns new sessions to the
  eligible backend with the fewest active requests (shared URL/model resource),
  using target priority/declaration order for ties. The default remains
  `priority`; session reservations and measured promotion retain continuity.
  Routing events include policy and per-candidate active/capacity snapshots.

### Fixes
- After fallback succeeds, affinity, cache observations and idle reservations
  are attributed to the backend that actually served the response rather than
  the initially selected target.
- `krm stop/restart` no longer implicitly escalates to SIGKILL. Shutdown wait
  defaults to 60 seconds (`--stop-timeout` / `KRM_STOP_TIMEOUT`, 0 = unlimited).
  An expired wait retains the PID and prevents restart from launching a second
  process; only explicit `--force` may kill a verified KRM process.
- Resolve output-token budgets consistently for summary planning and upstream
  requests: explicit overrides win over client values, then inherited route/
  root defaults. Context clamping no longer depends on a global completion
  default or overwrites an already-applied override with the original client
  value.
- Requests omitting `max_tokens` now receive the resolved default (4096 when
  all settings are omitted), rather than silently delegating to backend defaults.
  Locally rejected budgets release admission and any owned half-open probe
  without recording a backend failure. Root budget defaults follow hot reload.

## v0.9.5

### Added
- Opt-in `multimodal_validator.strip_all_files` removes structured file/PDF
  attachments throughout conversation history while preserving text and images.
- JSON log viewer aligned with current event envelopes and the server's plain
  formatter; request-ID, original-route and resolved-backend filters correlate full
  requests across balancing/fallback, with event globs and rotation-aware follow.
- Repository-wide Ruff lint/format checks and the automated test suite now run
  in development-repository GitHub Actions for pushes and pull requests.
- Configuration and troubleshooting references now cover all supported
  inference overrides, request/error capture boundaries and restart semantics,
  reasoning-history responsibility, and truncated tool-call arguments.
- Public onboarding now includes a consolidated HTTP API reference, OpenAI SDK
  and LibreChat examples, a routing guide in the README index, and a short
  glossary for core proxy concepts.
- README presentation now highlights prioritized local/multi-GPU serving,
  sticky sessions, configurable balancing/admission policies, and mixed
  LAN/public access with an explicit network-policy example.
- Smart routing with explicit reroutes, balanced target selection, bounded
  session affinity, backend capacity/admission state, reservation aging, and
  request-boundary promotion when a better target is worth the cold-prefill
  cost.
- Configurable override preservation and filter preservation across balance
  reroutes.
- Filter modules now compose by name across multi-parent `extends` chains, so a
  later parent that configures one filter no longer discards other inherited
  filters.
- Default-on, configurable compaction handoff for balanced agent routes: after
  a near-context-limit long decode, a sharply smaller prompt arriving shortly
  from the same route/client scope can inherit the prior backend reservation
  without requiring a client session ID. A one-shot continuation also keeps
  the backend across the next short-lived rewrite of the post-compaction prompt.
- Configurable client network access policies: routes can be LAN-restricted,
  public only with effective API keys, or intentionally open. Forwarded client
  IPs are honored only from explicitly trusted proxies; `/routes` and
  `/metrics` follow the global network policy.
- Recognized CUDA/device runtime failures mislabeled by an upstream as HTTP 400
  now trigger configured fallback routes; ordinary client 400 errors such as
  context overflow remain terminal.

### Fixes
- Ordinary upstream HTTP 400 errors in streaming no longer count as backend
  circuit failures. They still reach the client unchanged and complete any
  half-open health probe, matching non-streaming behaviour. Recognized CUDA
  failures mislabeled as 400 still trigger backend failure accounting/fallback.
- RLS/TLS recovery now preserves the original upstream request, tools, auth
  headers and route timeout. Retry budgets and detection thresholds are
  independent, configured intervention messages are honored in both serving
  modes, failed retries cannot become empty successes, and real truncation
  reasons remain visible. Non-streaming regenerated responses are revalidated
  through the response pipeline with bounded budgets and complete retry
  accounting/diagnostics. Streaming checks current calls against history and
  pairs synthetic tool results with their assistant calls.
- Non-streaming responses now honor tool-call suppression by RLS/TLS and other
  response filters. Replaced responses no longer leak the original upstream
  tool calls or reasoning; downstream finish reasons and completion logs match
  the filtered result, while genuine token-limit truncation remains visible.

## v0.9.4

### Added
- Native structured tool-call deltas can now reach compatible clients during a
  streaming turn instead of always waiting for `finish_reason: tool_calls`.
  Exact Tool Loop Stopper protection releases calls early only when they are
  provably distinct; conservative loop policies retain terminal buffering.
- Optional per-route admission control: `max_concurrent` and `queue_timeout`
  bound route work and expose active, pending, queued and rejected state via
  the private `GET /routes` status endpoint.
- Optional per-route circuit breaking with observable `CLOSED`, `OPEN`, and
  `HALF_OPEN` state and bounded recovery probes.
- `observability.privacy_mode` prevents request/model content from reaching
  PLAIN, JSON, raw-trace and capture persistence while retaining operational
  route, usage and latency metadata.
- Fallback observability now records the fully resolved retry chain and every
  decision's source/target route, model and endpoint, attempt position, and
  trigger (HTTP status, timeout, transport error, or open circuit).

### Changed
- The test suite has a 120-second default per-test timeout, with documented
  opt-outs only for deliberately longer integration scenarios.

### Fixes
- A route fallback now resolves and uses the fallback route's own upstream URL,
  model, headers, and timeout. It is applied to streaming pre-output connect/
  HTTP failures as well as non-streaming failures; no retry occurs after
  client-visible streaming output has begun.
- Upstream HTTP and SSE failures are returned as upstream errors rather than
  becoming an empty successful assistant response.
- Upstream application errors embedded in an HTTP-200 SSE stream now terminate
  the response as errors instead of being silently discarded. Terminal chunks
  that also contain the final tool-call delta retain that delta.
- An upstream HTTP `400` is treated as a terminal client-request error: KRM
  preserves its OpenAI-compatible error envelope and does not consume a
  fallback attempt. Failures recovered by a later fallback remain visible in
  structured diagnostics and raw capture.
- A Tool Loop Stopper fallback now continues through Model Nudge when it
  matches the configured nudge condition.
- `default_request_timeout` remains intact when it is inherited through route
  composition, rather than silently becoming the legacy 120-second value.

## v0.9.3 — 2026-08-29

### Added
- Route-aware client API-key access control. Global and inherited route
  `api_keys` accept standard `Authorization: Bearer <key>` credentials without
  forwarding the client secret upstream.
- Private `GET /routes` status API for trusted operator networks. It exposes
  public route configuration, rolling activity/errors/performance, and current
  pending/active requests without rescanning complete metric history per call.
- A bounded, shared upstream HTTP transport with explicit pool policy and
  observable startup configuration.
- Independent bounded projection workers for PLAIN, JSON, compact, and
  performance sinks, so slow filesystem work cannot block streaming responses.
- Process-wide `request_limits.max_body_bytes` protection (64 MiB by default)
  for both declared and chunked JSON request bodies.
- Graceful application shutdown now cancels the config watcher and closes the
  shared upstream client before stopping observability workers.
- Route-level `reasoning_effort` overrides forward LibreChat-compatible
  reasoning controls to Qwen and other OpenAI-compatible upstreams.
- Each completed request emits an `execution.chat.performance_metrics` PLAIN
  event with the same derived values persisted for the performance dashboard.

### Changed
- Public onboarding now uses a verified Python/venv setup path, canonical
  `filters:` configuration examples, and a fake-backend quick-start E2E test.
- `config.example.full.yaml` is the single complete public configuration
  reference; historical streaming design documents are dev-only.
- Performance records now retain logical prompt tokens separately from
  `cached_prompt_tokens` and `uncached_prompt_tokens`.
- Streaming cancellation, downstream send failures, and upstream lifecycle
  transitions have structured diagnostic events and E2E regression coverage.

### Fixes
- **V2 NudgeContinuationFinalizer recovery** now sends `request_payload_patch`
  using the `messages` key. The previous `nudge_message` key was silently
  ignored by `_apply_request_payload_patch()`, causing retries to reuse the
  original payload and eventually fall back. Nudge continuation now appends
  the lazy assistant prefix and continuation prompt to the upstream request.
- `prompt_tps` and `total_tps` no longer count KV-cached prompt tokens as
  prefill work, avoiding inflated throughput values for long cached contexts.
- Stream processing now promptly closes and cancels the upstream request when a
  downstream client disconnects, preventing orphaned backend generations.
- Streaming progress accounts for reasoning and tool-call content, and avoids
  misleading TPS estimates for a too-small initial decode segment.

## Unreleased

No unreleased changes yet.

## v0.9.1 — 2026-06-27

Bug fixes and remote API support.

Additions:
- Route-level `api_key` field for remote OpenAI-compatible providers (DeepSeek, OpenRouter, etc.)
- `api_key` automatically flows to filter retry calls
- API authentication via `upstream_headers` or `api_key`
- Reasoning content pass-through (`delta.reasoning_content`) during nudge buffering
- Flexible timestamp footer regex (supports any template format)
- Orphaned buffer recovery on stream exhaustion

Fixes:
- SSE keepalive no longer kills the generator chain (`asyncio.wait_for` → `asyncio.wait FIRST_COMPLETED`)
- Nudge filter no longer flushes on intermediate tool_call/reasoning chunks
- TimestampFilter replaces stale timestamps instead of skipping (also added flexible regex)
- Phase 4 no longer duplicates content or yields stale timestamps
- Tool_call finish_reason yielded in a separate chunk (LibreChat compatibility)
- Tool_call fragments merged by index with delta arguments concatenation
- Nudge buffer saved before retry (original lazy content preserved in Phase 4)
- LibreChat tool_call execution fixed (raw fragments + finish_reason pass-through)
- `_extract_assistant_text` restored (was accidentally deleted by sed)
- `pipeline_phase4_error` added to log whitelist
- Route `ctx_len` / `max_tokens` inheritance through extends chain
- Direct streaming fallback now includes model response in error finish

## v0.9.0 — 2026-06-25

Initial stable release.

Key features:
- OpenAI-compatible chat completions endpoint (streaming + non-streaming)
- Composable filter pipeline with pluggable filters
- Rolling summarization for context window management
- Route system with pattern matching and extends inheritance
- Structured BASIC_PLAIN logging

Filter pipeline:
- SystemPrompt: inject/override system prompt
- ModelNudge: lazy response detection and auto-retry
- ToolRewrite: XML pseudo-tool-call → structured tool_calls
- ToolLoopStopper: detect repeated tool call loops
- ReasoningLoopStopper: detect repeated reasoning patterns
- Timestamp: inject UTC timestamp
- MultimodalValidator: validate multimodal requests

See README.md for installation and usage.
