# Routing, Balancing & Concurrency

**Status:** current implementation reference for the 0.9.6 routing layer.
For configuration snippets, begin with [Configuration](CONFIGURATION.md); this
document defines the operational semantics behind those settings.
See `CHANGELOG.md` and the installed version's release tag for version-specific
changes.

This document describes how KeepRoLLMing (KRM) resolves a client request to a
concrete upstream backend and how it governs access when multiple requests and
routes compete for the same backend resource. It covers the full path from
`client_model` to an upstream call: **route resolution → smart-routing
selection → compatibility checks → session affinity/reservation → admission
control → circuit breaking → execution**.

The implementation lives primarily in:

| Area | Module |
|---|---|
| Route resolution, inheritance, fallback, reroute | `keeprollming/routing/router.py` |
| Balance selector, target graphs, selection policies | `keeprollming/routing/balancing.py` |
| Per-request immutable execution plan | `keeprollming/routing/plan.py` |
| Static request requirements (capabilities, context fit) | `keeprollming/routing/requirements.py` |
| Session affinity & idle reservation | `keeprollming/routing/affinity.py` |
| Shared backend-resource identity & coherence | `keeprollming/routing/resources.py` |
| Admission gates, priority/aging/deadline, fallback transfer | `keeprollming/admission.py` |
| Circuit breaker | `keeprollming/circuit_breaker.py` |
| End-to-end integration point | `keeprollming/endpoints/chat_completions.py` |

---

## 1. Overview: a layered pipeline

KRM does not use a single router. It is a pipeline of layers, each with a
distinct responsibility:

```
client_model
   |
   v
[1] Route resolution          first-match-wins (pattern) + `extends` inheritance
                               + DEFAULT_FALLBACK_ROUTE terminal
   |
   v
[2] Authentication            against the REQUESTED route's client credentials
   |
   v
[3] Smart-routing selector    (only when the route declares `balance`)
      - derive request requirements (capabilities, token estimate)
      - session-affinity lookup (hint)
      - eligibility predicate per target (compatibility, reservation,
        circuit, capacity)
      - deterministic ordering: priority desc, then declaration order
        (least_loaded optionally ranks active counts before those tie-breaks)
      - bounded wait/re-evaluation within the queue budget
      - reservation bypass at the deadline
   |
   v
[4] Attempt compilation       all balance leaves (+ each leaf's own fallback)
                               then the selector's fallback, deduplicated
   |
   v
[5] Admission control         per-backend-resource priority/FIFO gate
                               (+ queue aging, + first-token deadline boost)
   |
   v
[6] Circuit breaker           CLOSED / OPEN / HALF_OPEN on the backend resource
   |
   v
[7] Execution                 RoutePlan + filter chain + upstream call; on
                               failure FallbackAdmission transfers the lease
```

Two ideas run through the whole design:

- **The requested route and the selected target are different objects.** The
  client is authenticated against the *requested* route, and that route's
  *service class* (`routing_priority`, `queue_timeout`, `queue_aging`,
  `deadline_policy`) governs how hard the client is willing to wait. The
  *selected target* owns admission capacity, upstream credentials, filters and
  all execution settings.
- **The unit of concurrency governance is the backend resource, not the route.**
  A backend resource is the pair `(upstream_url, model)`. Every route that
  resolves to the same resource shares one admission gate and one circuit
  breaker, so logical routes that point at the same physical backend contend
  together.

---

## 2. Route resolution

`resolve_route_from_routes()` in `routing/router.py` maps a client-facing model name to a
route and backend model.

### Pattern matching

`_parse_pattern()` accepts, in order of preference:

- **Wildcard glob** — `pass/*` becomes `^pass/(.*)$`.
- **Top-level alternation** — `a|b|c` (only when the pattern contains no
  parentheses) becomes an anchored alternation.
- **Advanced regex** — if the pattern already contains regex metacharacters it
  is used verbatim (wrapped in `^…$`); an invalid regex degrades to an escaped
  literal.
- **Exact match** — otherwise the string is escaped and matched literally.

Matching is **first-match-wins** over the user-defined route list.

### Model extraction

`_extract_model()` / `_apply_capture_pattern()` recover the concrete backend
model from a match. A route may pin `model`, or use `model_pattern` with
capture groups (`${1}`, `$1`, `${name}`, `$name`, and prefixed/suffixed forms
such as `${1}/suffix`). Passthrough routes derive the model from the client
request at resolution time.

### Inheritance (`extends`)

`resolve_inherited_route()` composes a route's full configuration from its
parents. Rules:

- The **child's explicit value always wins**; `None` falls through to the
  parent, and so on up the chain.
- `overrides` merge per-key (child keys override parent keys).
- `filters` merge per filter module: parents compose left-to-right, later
  parents override matching module names, and the child has final precedence.
  Unspecified filter modules are retained; disable one explicitly with
  `enabled: false`.
- **Defaults are applied only on the top-level call** so that `None` values
  propagate correctly through intermediate parents.
- Circular inheritance is detected and stops recursion.
- `cost_priority` is currently accepted and inherited for configuration
  compatibility, but has **no runtime effect** on either balancing or fallback
  order. Do not use it to express a service policy; a future cost-aware policy
  will have an explicit contract.

### Fallback chains

`resolve_fallback_chain()` returns the ordered `(route, model)` attempts to try
when the primary backend is unavailable. The primary is first; up to **three**
additional fallback attempts follow. Each fallback entry is either a route name
(resolved through its own inheritance) or a raw model on the same route.
Attempts are de-duplicated by `(route, model)`.

### Reroute destinations

`resolve_reroute_destination()` resolves a *target* route independently of the
source route (used by balancing and explicit delegation). It enforces a maximum
depth of **8**, detects cycles, and refuses to target **private** routes. A
destination's filters, credentials, admission settings and fallback chain are
never merged back into the source.

### Default fallback

If no user route matches, the request is served by `DEFAULT_FALLBACK_ROUTE`,
optionally given the root-level `upstream_url`.

### One serving slot by default

`DEFAULT_MAX_CONCURRENT = 1`. A concrete upstream is treated as a single
serving slot unless the operator configures a different limit. This is
deliberate: an omitted limit must not make a busy backend appear indefinitely
available to every selector.

---

## 3. Balance / smart-routing selector

A route that declares `balance` becomes a **selector**: for each request it
points at exactly one *concretely resolved* target route. Without `balance`, a
route is **direct** and selects itself (its concrete model is obtained from the
client match at resolution time).

Configuration shape:

```yaml
balance:
  enforce_capabilities: false          # default; make capability declarations hard
  targets:
    - route: code/runner-a
      priority: 100
    - route: code/runner-b
      priority: 80
  preserve_overrides: [reasoning_effort]
  # Carry only these source-route filter modules into the selected target.
  # The named source configuration wins for that module; other target filters
  # remain target-owned.
  preserve_filters: [tool_rewrite]
  affinity_ttl_seconds: 3600
  idle_reservation:
    enabled: true
    mode: session                      # or "priority"
    timeout_seconds: 900
    min_prompt_tokens: 4096
    min_cached_prompt_tokens: 2048
  selection_retry_interval_ms: 250
  allow_reservation_bypass: true
```

### Target ordering

`balance.selection_policy` defaults to `priority`. Set it to `least_loaded`
to assign a new session to the eligible leaf with the fewest **active requests**:
zero beats one, one beats two, and ties use the existing priority/declaration
order. The counts aggregate all KRM routes sharing `upstream_url + model`;
they are not GPU utilization or `active / max_concurrent` ratios. Capacity,
queued work, circuit health, capabilities/context and reservations still gate
eligibility. Counts are local to this KRM process, not external backend traffic.

An eligible session-owned reservation still keeps its target, even if another
target is idle. The existing measured promotion policy remains priority-based;
least-loaded selection does not trigger migrations on its own. If the pinned
target is unavailable, reassignment uses the selected policy. Each selection
retry refreshes counts; admission remains authoritative. Static resolution
(including `/routes`) retains declarative priority order without runtime load.
The outer selector's policy ranks the expanded leaves of nested balance pools.

```yaml
balance:
  selection_policy: least_loaded  # or priority (default)
  targets:
    - {route: pool/fast, priority: 100}
    - {route: pool/slow, priority: 80}
```

`execution.routing.balance_decision` includes `selection_policy` and
`candidate_loads` (`active`, `capacity`, `queued`), sampled before admission.
BASIC plain logs show `policy`, `reason` and compact `load=[route:active/capacity]`.

`parse_balance_targets()` normalizes the list and orders targets
**deterministically: higher `priority` first, then declaration order**. Targets
must be non-empty route names and must not be duplicates or private routes.
An explicit `targets: []` clears an inherited balance policy (inheritance-safe).

### Target-graph expansion

A target may itself have a `balance`, so `_expand_leaf_targets()` recursively
flattens the graph into independently resolved **leaves** (bounded at depth 8,
de-duplicated by `(route, model)`). A leaf that has no further targets is a
terminal concrete route.

### Selection

`resolve_balance_selection()`:

1. If the route has **no targets**, return the route itself unchanged (direct).
2. Otherwise, order the unique leaves (priority, then declaration; prepend
   active request count for runtime `least_loaded` selection). If a
   *preferred* route is supplied (session affinity, §5), it is sorted to the
   front.
3. Return the **first leaf for which `is_eligible` is true**.
4. If no leaf is eligible, fall back to the *source* route's `fallback_chain`
   (skipping known-ineligible entries) — the **selector fallback**.
5. If nothing is eligible, raise `NoEligibleBalanceTarget`.

`BalanceSelection.compile_attempt_routes()` then produces the retry order:
every balance leaf (each retaining its own fallback chain) followed by the
selector's fallback, de-duplicated.

### Selection policies (validated at startup)

| Policy | Parser | Default | Effect |
|---|---|---|---|
| `selection_policy` | `parse_selection_policy` | `priority` | `least_loaded` ranks active shared-resource counts before target priority/declaration; pins and measured promotion remain intact. |
| `promotion` | `parse_promotion_policy` | 5 additional turns; 8192 new prompt / 1024 output tokens | Fixed future prefill/decode assumptions; `lookahead_turns: 0` restores current-only estimates. |
| `enforce_capabilities` | `parse_capability_enforcement` | `false` | Capability declarations are **advisory** by default; `true` makes them a hard admission contract. Context-fit is always hard. |
| `preserve_overrides` | `parse_preserved_override_keys` | none | Named source-route overrides are re-applied to the selected target's upstream payload (vocabulary limited to `ALLOWED_OVERRIDE_KEYS`). |
| `preserve_filters` | `parse_preserved_filter_names` | none | Accepts a list of source filter names or `all`/`true`; source config wins on collisions and target-only filters remain. |
| `affinity_ttl_seconds` / `idle_reservation` | `parse_reservation_policy` | 3600s; enabled, `priority` mode | Session-affinity lifetime and idle-resource leasing (see §5). |
| `selection_retry_interval_ms` / `allow_reservation_bypass` | `parse_balance_wait_policy` | 250ms; `true` | How the selector behaves while every valid target is busy (see §6). |

`validate_balance_policies()` runs at startup and proves every target graph
resolves (route existence, private-target exclusion, cycles, leaf model
availability) before the config becomes live.

---

## 4. Compatibility / request requirements

`derive_request_requirements()` extracts **non-identifying** request facts used
by the eligibility predicate (it never retains message text):

- **Capabilities** — `tools` (request has a `tools` list), `vision` (any image
  part), `reasoning` (thinking requested via `enable_thinking` / `thinking` / a
  non-disabled `reasoning_effort`).
- **Prompt token estimate** — counted by `TokenCounter`, plus `1024` per image.
- **Requested completion tokens** — from `max_tokens` / `max_completion_tokens`.
- Route `overrides` for `reasoning_effort` / `max_tokens` are applied first, so
  the selector never chooses a target that cannot honour the route's own
  reasoning/output policy.

`RequestRequirements.incompatibility_reason()` returns a single stable reason,
or `None` when compatible:

- `missing_capability:<cap>` — only when `enforce_capabilities` is on.
- `context_insufficient` — when `prompt_tokens + completion + safety_margin >
  ctx_len`. This is **always** a hard safety check.

---

## 5. Session affinity & idle reservation

`SessionAffinityRegistry` (`routing/affinity.py`) is a process-local,
thread-safe, **bounded** (default 4096 entries) registry. It is
**privacy-preserving**: it stores only HMAC-derived opaque keys and aggregate
token/context facts, never message content. Assignments and reservations have
deliberately **different lifetimes** — remembering a conversation lasts longer
than holding a resource.

### Affinity keys

For a multi-request shared backend, declare `allow_idle_reservation: false`
on its concrete route (default `true`). This resource-owned veto overrides
every source's idle-reservation policy: it removes existing leases on reload
and blocks new ones, including from requests already in flight. Aliases of the
same normalized URL/model must agree. It does not disable affinity, cache
observations, compaction continuity or measured promotion back to a better GPU.
There are no per-lane reservation guarantees: backend-internal cache/slot
assignment remains the backend's responsibility.

Normal selection first tries free eligible targets. If none are free, a full
shared target can accept the request into its actual admission queue, within
the source's remaining positive `queue_timeout`. Source service priority,
aging and deadline ordering apply there; running requests are never preempted.
That queue is bound to the chosen resource; it does not reselect the entire
pool while waiting. A zero wait budget still rejects immediately. Dedicated
targets retain the existing reservation/selector-wait behavior.

`candidate_keys()` produces, strongest first:

1. A **client-conversation** key from `x-librechat-conversation-id`, scoped by
   an HMAC of the client credential (so two authenticated clients with
   identical prompts cannot influence each other's pin).
2. **Message-prefix** digests at message boundaries (up to 128 prefixes), so a
   later turn that contains earlier turns can find the previous full prompt
   without retaining raw content.

### Lookup and assignment

- `lookup()` returns a live assignment only if its target is still in the
  selector's current **declared target set**; dynamic eligibility (capacity,
  reservation, circuit and compatibility) is evaluated afterwards. States are
  `hit`, `cache_hit`, `miss`, and `ineligible`.
- `assign()` records the selected target for the session with a TTL (default
  1h). A stale assignment is harmless — it is used only if the target remains
  eligible.
- `balance.promote_when_beneficial` defaults to `true`. At a request boundary,
  a session with a live reservation or affinity on a non-reservable shared
  backend may move to a higher-ranked eligible target
  before expiry if recent measured prefill/decode rates predict a net gain.
  The estimate charges the destination for a cold prefill and credits the
  current target only for upstream-reported cached tokens. It uses KRM's
  estimated prompt size, not backend tokenizer details. It also includes
  future prefill/decode gains using `balance.promotion` (details below).
  Metrics or a completion
  token budget missing means KRM keeps the current assignment. Setting the flag
  to `false` restores lease-expiry-only promotion. After capacity is acquired
  at the new target, KRM transfers the assignment and releases the old idle
  reservation. In-flight requests and streams are never moved.

### Promotion horizon and cost

```yaml
balance:
  targets: [pool/fast, pool/slow]
  promote_when_beneficial: true
  promotion:
    lookahead_turns: 5
    expected_new_prompt_tokens: 8192
    expected_completion_tokens: 1024
    expected_current_completion_tokens: 1024
```

These are the defaults. `lookahead_turns` counts **additional equivalent
turns**, accepts finite non-negative numbers/fractions, and may be zero to
restore current-turn-only behavior. Expected token volumes are non-negative
integers; zero disables that future prefill/decode contribution.

For prompt `P`, upstream-reported cached prefix `C`, current output forecast
`B = min(output budget, expected_current_completion_tokens)`,
future new prompt tokens `N`, future output `O` and equivalent turns `L`:

```text
stay = (P - C) / PP_current + B / decode_current
       + L * (N / PP_current + O / decode_current)
move = P / PP_destination + B / decode_destination
       + L * (N / PP_destination + O / decode_destination)
```

`expected_current_completion_tokens` defaults to 1024 and is independent of
future-turn `expected_completion_tokens`. It is a non-negative integer operator
assumption (zero prices no current decode). It does not alter upstream output
limits. Events retain both `completion_tokens_budget` and
`completion_tokens_forecast`, so the safety cap cannot be mistaken for the
prediction. This remains a configurable heuristic, not an adaptive predictor.

With recent evidence, promote only if `move < stay`; ties stay pinned.
With historical or configured evidence, require respectively more than 20%
or 35% savings relative to `stay` (configurable). Use the larger margin when
sources differ. The migration's cold prefix
cost is paid once, not multiplied by future turns. Both future alternatives
assume a retained warm prefix and process only newly appended tokens. A faster
decoder with slower prefill can therefore lose on prompt-heavy future work.
These are fixed configurable estimates, not a prediction of actual session
length, future cache eviction, context compaction or context-dependent rates.

The new projection has O(1) arithmetic/memory, no per-turn loop and no new
tokenization, disk I/O, recent-request parsing or session-history storage.
PP/decode evidence is keyed by normalized upstream URL + model, not route name:

| Source | Default eligibility | Confidence / extra gain threshold |
|---|---|---|
| Recent | 3 valid observations within 1 hour | high / positive gain |
| Historical | 6 retained observations; latest <=24h old; PP and decode CV <=0.5 | medium / >20% of stay time |
| Configured | backend route `performance_hint` with both positive rates | low / >35% of stay time |
| Unavailable | none of the above | stay pinned |

Example backend hint (optional, inherited by `extends`, never sent upstream):

```yaml
pool/fast:
  upstream_url: http://worker:8080
  model: local-model
  performance_hint:
    prompt_tps: 2000
    completion_tps: 80
```

Disable an inherited hint with `performance_hint: {enabled: false}`. Tune the
hierarchy via `balance.promotion.metrics`; all defaults appear in
`config.example.full.yaml`. Confidence labels describe the evidence tier, not
a statistical guarantee or a context-length-specific performance model.
Rates still use KRM's canonical performance calculations, not backend-native
GPU timing. No adaptive future-volume estimator is introduced.

Aggregates update at successful single-attempt completions; aliases share them.
Legacy unidentified records, failures/cancellations and mixed-attempt totals
are excluded. Two rolling moment sets per resource allow constant lookup and
amortized expiry, capped at 128 resources / 1024 observations per window.
History retains at most 7 days; configurable freshness is the age of its latest
observation, not the age of every observation. Atomic `routing_estimates.json`
under the performance log directory is saved at graceful shutdown, restored at
startup and supplemented by identified recent root performance records without
duplicating snapshot observations. Forced exits may lose newer history. Snapshot
corruption is diagnosed and ignored; no log scan or disk I/O occurs in requests.

`promotion_estimate` logs `assumptions_source: configured`, horizon/volumes,
current-turn and full-horizon times, weighted future prefill/decode savings,
net savings and decision. Negative savings are retained, not discarded.
It also includes `current_evidence` / `candidate_evidence` (source, sample
count, age, variability and confidence or an unavailable reason), plus
`required_gain_ratio` / `required_gain_seconds`. These nested facts are in the
detailed routing event; BASIC keeps its compact selection summary.
`/routes` exposes the effective promotion assumptions in `balance_policy`.

### Following routing in default BASIC PLAIN logs

- `execution.routing.balance_waiting` appears once when a selector starts
  waiting for capacity/reservations, with wait budget and blocked candidates;
  polling retries are not printed.
- `execution.routing.balance_decision` records the selected route/backend,
  reason, affinity/reservation state and, after waiting, `queued_ms`.
  Evaluated promotion decisions are named explicitly; successful promotions
  also show `from`, estimated `gain_s` and evidence tiers. The full cost/evidence
  dictionaries remain in detailed structured events.
- `request.lifecycle.admission.queued` and the matching waited `acquired`
  show entry/exit for the backend admission queue, service route, wait duration
  and original/effective priority (including aging/deadline effects). Immediate
  acquisitions and routine releases stay below BASIC to avoid noise.
- Rejections, cancellations and existing fallback events close the flow.
  A selector queue and a concrete backend queue are different stages; the
  shared request ID links them. Promotion happens only at request boundaries,
  never by interrupting the current upstream turn.

Illustrative BASIC lines (timestamps and unchanged fields abbreviated):

```text
[req] execution.routing.balance_waiting route=code/main budget_ms=60000 blocked=[pool/a:capacity_unavailable]
[req] execution.routing.balance_decision requested_route="code/main" selected_route="pool/b" ... queued_ms=350 promotion=promote from=pool/a gain_s=12.5 evidence=recent/historical
```

`gain_s` is estimated savings, not a measurement of realized performance.

### Compaction handoff

`balance.compaction_handoff` defaults to enabled. It recognizes the observed
  agent-compaction shape: a completed turn near the context limit with a long
  decode, followed shortly by a much smaller prompt on the same requested
  route and client-origin/credential scope. Since clients do not expose a
  stable session ID for this transition, this is explicitly a heuristic, not
  proof of session identity. A match is consumed once: KRM invalidates the old
  prefix assignment, transfers any idle reservation to the new prefix owner,
  and prefers the same physical backend for that request. Promotion is deferred
  for this handoff turn. If that first post-compaction request succeeds, KRM
  arms one additional, short-lived `compaction_continuation`: a subsequent
  prompt rewrite can keep the backend when the first system message still has
  the same first and last 1,024 characters. The comparison uses an opaque
  route/client-scoped HMAC, not persisted prompt text. This second bridge is
  consumed once, expires under the same `max_age_seconds`, and does not prove
  that the requests belong to one session. A shared system prompt can produce
  a false association within the same client scope. Ordinary promotion can
  resume at a later request boundary.
  Health, capacity, route compatibility and circuit-breaker checks still apply.
  Defaults are a 60-second window, at least 80% context occupancy, at least
  5,000 completion tokens, and at least a 50% prompt-size reduction. Set
  `enabled: false` to disable it; all four thresholds are configurable. Keeping
  the same backend does not guarantee a full prefix-cache hit after compaction.

### Idle reservation

After a successful turn, `reserve()` leases the selected idle resource to that
session:

- **Duration** — a fixed `timeout_seconds` if set; otherwise it scales
  linearly between `min_seconds` (default 60s) and `max_seconds` (default
  900s) based on the **context ratio** (`prompt_tokens / ctx`), clamped to
  `[min_context_ratio, max_context_ratio]` (default 0.10–0.75).
- **Minimums** — `min_prompt_tokens` / `min_cached_prompt_tokens` avoid
  leasing a backend after a tiny turn or a cold (uncached) prompt.

`reservation_status()` reports, for a candidate resource relative to the
request's service priority:

| State | Meaning |
|---|---|
| `owner` | this session already holds the reservation |
| `reserved_by_other_session` | a `session`-mode reservation held by another owner |
| `reserved_by_higher_priority` | a `priority`-mode reservation at a strictly higher class |
| `preemptable` | a `priority`-mode reservation at a lower class |
| `equal_priority` | a same-class `priority` reservation |
| `none` / `not_applicable` | no reservation / no resource key |

Preemption rules: a **`priority`** reservation protects only against a
*strictly lower* class; a **`session`** reservation protects the resource from
*every* other owner until expiry. A higher-priority session may preempt a
priority reservation but never a session reservation.

---

## 6. Wait / queue budget and reservation bypass

The selector does not fail immediately when every valid target is busy
(`chat_completions.py`):

1. If the session's reservation is a live **`owner`**, the selector normally
   prefers that route (`preferred_route_name`), reason `reservation_affinity`.
   With `promote_when_beneficial: true`, it may instead select the normal
   highest-ranked eligible target when the measured performance estimate
   exceeds the cold-prefill migration cost (`promotion_beneficial`). If the
   lease expires, normal ordering resumes regardless of the flag (`promotion` /
   `waited_for_capacity`).
2. If neither a balance leaf **nor the selector's fallback chain** is
   immediately eligible, but some balance targets are in a *waitable* state
   (`capacity_unavailable`, `reserved_by_higher_priority`,
   `reserved_by_other_session`) and queue budget remains, the selector **sleeps**
   `selection_retry_interval_ms` (default 250ms) and **re-evaluates all
   candidates** in a loop. The budget is `queue_timeout` measured from the
   monotonic request-ingress mark, not from the start of this loop. The
   subsequent admission gate receives only the remaining budget: selection and
   admission never each receive a full independent timeout.
3. At the deadline, if `allow_reservation_bypass` is set, the selector bypasses
   the **least-cost still-idle reservation** (ordered by mode
   `priority`→`session`, then lower reservation priority, lower context ratio,
   and earlier expiry) rather than
   returning an error. **Capacity, health and compatibility are never
   bypassed.**
4. If still nothing is eligible, `NoEligibleBalanceTarget` is raised. The
   endpoint returns **`429 route_capacity_exceeded`** (with `Retry-After: 1`)
   when capacity was the cause and `queue_timeout > 0`, otherwise **`503
   route_selection_error`**.

---

## 7. Admission control

`RouteAdmissionController` (`admission.py`) owns a bounded, deterministic
priority **gate** per backend resource and per event loop. The gate is keyed by
`(loop_id, resource_key or route_name, max_concurrent)`, so all routes that
resolve to one resource share one gate.

### The gate

`_AdmissionGate` has a `capacity` (`max_concurrent`) and an `active` count:

- `has_available_capacity` is true only when `active < capacity` **and there are
  no waiters** — a new request can never cut in front of queued work.
- `acquire_or_enqueue()` grants a slot immediately if free; otherwise it enqueues
  a `_Waiter`.
- On `release()`, the next waiter is chosen by the key
  `(-effective_priority(now), order)` — **highest effective priority first, FIFO
  among ties**. A granted lease is never preempted; a new arrival never
  bypasses the queue.

### Effective priority

```
effective_priority = routing_priority        # service class of the REQUESTED route
                   + aging_boost            # bounded starvation protection
                   + deadline_boost         # advisory first-token pressure
```

- **`routing_priority`** (default `0`) comes from the *requested* route, so
  different client-facing service classes contending on one backend are ordered
  by it.
- **Queue aging** (`QueueAgingPolicy`, disabled by default): every
  `interval_seconds` (default 30s) a waiter gains `priority_step` (default 1),
  capped at `max_boost` (default 20). It is evaluated only at request boundaries
  and never interrupts a live lease.
- **Deadline policy** (`DeadlinePolicy`, disabled by default): a server-owned,
  **confidence-gated** first-token SLO. It produces a boost only when there are
  enough historical TTFT samples (`min_samples`, default 5) and the first-token
  target is still feasible (`slack` within `urgency_window`, default 5s). An
  already-infeasible target gets no boost, so hopeless work never jumps the
  queue. `queue_timeout` remains the hard admission limit.

### Acquisition and rejection

`acquire()` waits on the waiter's future bounded by `queue_timeout`. On timeout
it releases (if already granted) or cancels the waiter and reports rejection.
The endpoint returns **`429 route_capacity_exceeded`** when a limited route is
at capacity.

### Fallback transfer

`FallbackAdmission` moves the request between backend resources during a
fallback: before contacting a fallback backend it **releases the failed
resource's lease and acquires the new one**, so capacity is neither overcommitted
nor leaked. If the fallback resolves to the **same** resource, no
release/re-acquire (and no self-queue) is needed. The transfer is a
request-boundary / pre-output operation only — never after client-visible data
has been emitted.

---

## 8. Circuit breaker

`CircuitBreakerRegistry` (`circuit_breaker.py`) tracks CLOSED / OPEN / HALF_OPEN
state **per backend resource**:

- After `failure_threshold` (default 3) counted upstream failures the circuit **opens**
  for `recovery_timeout` (default 60s).
- It then enters **half-open** with a single probe.
- `is_available()` (used by the selector) inspects state **without consuming**
  the half-open probe; the authoritative `allow()` is called after selection.
- A configured fallback model can still serve traffic while the primary circuit
  is open.

Failed upstream attempts can count even when a fallback later serves the client;
otherwise an unhealthy preferred target would be retried indefinitely. Ordinary
client-request `400` errors remain terminal and are not retried. In both serving
modes they do not penalize backend health: a valid HTTP client-error response
completes a half-open probe and resets its circuit, while the request still
finishes as an error and preserves the upstream error for the client.
Recognized CUDA/device runtime failures mislabeled as `400` are instead treated
as backend failures and can consume fallback attempts.

---

## 9. Shared backend-resource model

`backend_resource_key(route)` derives the physical identity
`(upstream_url with a trailing `/v1` stripped, model)`. Passthrough /
dynamic-model routes return `None` and are grouped only at request time.

`validate_shared_backend_resources()` (run at startup) requires that all routes
sharing a resource **agree** on `max_concurrent` and circuit-breaker settings
(`failure_threshold`, `recovery_timeout`). Per-route queue tolerance is
*not* compared, because it represents the client's willingness to wait rather
than a property of the shared backend. This coherence is what lets a single gate
and breaker safely represent the resource.

---

## 10. Per-request execution plan

`RoutePlan` (`routing/plan.py`) is an **immutable** snapshot of the resolved
decisions for one request: settings, upstream URL/endpoint, headers, frozen
filters, enabled filters, capabilities, fallback attempts and overrides. It is
the runtime boundary between configuration (`Route`) and execution, so the
streaming and non-streaming paths never independently re-read a mutable `filters`
configuration. `UpstreamAttempt` is one fully resolved retry target (route,
model, endpoint, headers, timeout).

---

## 11. End-to-end request flow

In `keeprollming/endpoints/chat_completions.py`:

1. **Resolve** the requested route (`resolve_route`).
2. **Authenticate** against the requested route's `api_keys` (a public route
   has `api_keys: []`).
3. **If balanced**: derive request requirements, look up session affinity,
   build the eligibility predicate, and run the bounded selection loop
   (§6) to pick the target. If not balanced, the target is the requested route.
4. **Compile attempts** and, for balanced routes, filter attempts by
   compatibility.
5. **Admit**: acquire a lease on the selected target's backend resource
   (§7); return `429` if a limited route is at capacity.
6. **Circuit check** (`allow`); if the primary is open, move to a fallback
   attempt (transferring the lease).
7. **Execute**: build the `RoutePlan`, run the filter chain, call upstream
   (streaming or non-streaming). On failure, `FallbackAdmission.move_to()`
   transfers the lease to the next attempt.
8. **Record** session affinity and, on a successful final response, reserve the
   idle resource (§5).

Admission happens **after** authentication but **before** logging, raw capture,
filtering, summarization and upstream allocation — so a busy route is cheap to
reject and a streaming slot is held until the ASGI body generator closes.

---

## 12. Behavior under default values

If nothing is configured, KRM is a **conservative, single-slot,
non-blocking, drop-on-busy** proxy. The advanced governance is entirely opt-in.

| Parameter | Default | Consequence |
|---|---|---|
| `max_concurrent` | `1` | one in-flight request per backend resource |
| `queue_timeout` | `None` (0 budget) | **no waiting** — a request that cannot get the slot is rejected immediately |
| `routing_priority` | `0` | all requests same class → FIFO (moot without a queue) |
| `queue_aging` | `None` | no starvation protection |
| `deadline_policy` | `None` | no first-token SLO pressure |
| `circuit_breaker_enabled` | `false` | no automatic isolation of a failing backend |
| `fallback_chain` | `[]` | no failover |
| `balance` | none / inherited | smart selector **off** → the route is *direct* |

Resulting behavior:

- **Direct route (the default case):** concurrency is governed only by the
  admission gate. With `max_concurrent = 1` and `queue_timeout = None`, a second
  concurrent request is **rejected immediately with `429`** — it is never queued.
  No breaker, no fallback: a busy or failing backend causes the request to be
  dropped or to surface the backend's own error.
- **Balanced route (you opted into `balance`, sub-policies at default):** the
  selector ranks targets (all priority 0 → declaration order) and treats a busy
  target as ineligible. Because `queue_timeout` is `None`, the **wait loop does
  not run**. If every target is busy and none is merely reservation-blocked, the
  selection fails with **`503 route_selection_error`** (it is `429` only when
  `queue_timeout > 0`). Session affinity (1h) and reservation (priority mode,
  `min_prompt_tokens = 0`) remain active but, without a wait budget, they do not
  hold capacity.

To obtain real concurrency governance you must opt in: set `max_concurrent`
(widen the gate), set `queue_timeout > 0` (this is the switch that enables
queueing and the wait loop), and add `routing_priority` / `queue_aging` /
`deadline_policy` for prioritization, anti-starvation and first-token SLO, plus
`circuit_breaker_enabled` and `fallback_chain` for isolation and failover.

---

## 13. Configuration reference

| YAML key (per route) | Governs | Default |
|---|---|---|
| `balance.targets[].route` / `.priority` | enable the smart selector; ordering (priority desc, then declaration) | none (direct route) |
| `balance.selection_policy` | `priority`, or `least_loaded` to rank active request counts first | `priority` |
| `balance.enforce_capabilities` | make capability declarations a hard constraint | `false` |
| `balance.preserve_overrides` | re-apply named source overrides on the target | none |
| `balance.preserve_filters` | carry named source filter modules into the target pipeline | none |
| `balance.affinity_ttl_seconds` | session-assignment lifetime | `3600` |
| `balance.promote_when_beneficial` | allow measured, request-boundary promotion before reservation expiry | `true` |
| `balance.promotion.lookahead_turns` | equivalent additional turns in promotion estimate; 0 = current only | `5` |
| `balance.promotion.expected_new_prompt_tokens` | assumed new prompt tokens per future turn | `8192` |
| `balance.promotion.expected_completion_tokens` | assumed output tokens per future turn | `1024` |
| `balance.promotion.expected_current_completion_tokens` | current-turn forecast, bounded by the available cap; never changes upstream max_tokens | `1024` |
| `balance.compaction_handoff.enabled` | infer a one-time affinity handoff after likely context compaction | `true` |
| `balance.compaction_handoff.max_age_seconds` | maximum time from long decode completion to the smaller prompt | `60` |
| `balance.compaction_handoff.min_context_ratio` | minimum old-prompt occupancy at the effective context limit | `0.80` |
| `balance.compaction_handoff.min_completion_tokens` | minimum decode size on the prior turn | `5000` |
| `balance.compaction_handoff.min_prompt_reduction_ratio` | minimum fractional prompt reduction on the next turn | `0.50` |
| `balance.idle_reservation.mode` | `priority` or `session` preemption class | `priority` |
| `balance.idle_reservation.timeout_seconds` | fixed lease duration (else scaled by context ratio) | unset |
| `balance.idle_reservation.min_prompt_tokens` / `min_cached_prompt_tokens` | do not lease after tiny/cold turns | `0` |
| `balance.selection_retry_interval_ms` | wait-loop interval | `250` |
| `balance.allow_reservation_bypass` | bypass the least-cost idle reservation at the deadline | `true` |
| `max_concurrent` | admission-gate capacity for the backend resource | `1` |
| `queue_timeout` | hard wait budget (admission + selection loop) | `None` |
| `routing_priority` | service-class priority among queued requests | `0` |
| `queue_aging.interval_seconds` / `.priority_step` / `.max_boost` | bounded starvation protection | disabled |
| `deadline_policy.enabled` / `.first_token_budget_ms` / … | server-owned first-token SLO pressure | disabled |
| `circuit_breaker_enabled` / `failure_threshold` / `recovery_timeout` | per-backend health breaker | `false` / `3` / `60` |
| `fallback_chain` | ordered post-failure retry (max 3) | `[]` |
| `capabilities` | static feature declarations for the selector + status | inherit |

---

## 14. Design principles

1. **Requested route vs. selected target.** Authentication and the client's
   service class belong to the requested route; admission, credentials, filters
   and execution settings belong to the concrete target.
2. **Backend resource is the unit of governance.** Capacity and health are
   keyed by `(url, model)`, so routes that share a backend contend together and
   must agree on shared settings.
3. **Determinism plus hints.** Base ordering is deterministic (priority, then
   declaration). Affinity and reservations are *hints* that can be preempted;
   capacity, health and compatibility are **hard constraints** that are never
   violated.
4. **No queue-cutting, no live preemption.** A new arrival cannot bypass queued
   work, and a live lease is never interrupted — arbitration happens only among
   queued requests at request boundaries.
5. **Advisory until there is evidence.** Capability declarations and the
   deadline policy stay neutral until the operator enables them or enough
   historical samples exist, so fresh configurations do not break routing.
6. **Admission before work.** Admission runs before logging, capture, filtering,
   summarization and upstream allocation, making busy routes cheap to reject and
   keeping a streaming slot held for the whole response.
