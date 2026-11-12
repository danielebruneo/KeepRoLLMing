# Upgrading KeepRoLLMing

## From 0.9.5 to 0.9.6

The existing YAML format is retained. Review the behavior changes below before
updating a deployment; new settings are optional unless you want their behavior.

### Output budgets are now applied consistently

When the client omits `max_tokens`, KRM sends the selected route's inherited
default, the root default, or the built-in **4096** fallback. Previously an
omitted budget could be delegated to the backend. Summary planning and the
outgoing request now use the same effective budget, with context clamping
independent of `default_max_completion_tokens`.

A route's `max_tokens` is a default, **not** a client cap. To force a value, use
`overrides.max_tokens`; explicit preserved source overrides take precedence
over selected-target overrides. Client values otherwise precede defaults.
Requests with no estimated output room return HTTP 400 instead of starting
the final generation. Backend validation remains authoritative: KRM's prompt
count is an estimate, not the backend's tokenizer/chat-template result.

Check your expected response length and summary threshold after upgrading.
See [output-token budgets](CONFIGURATION.md#output-token-budget-096).

### Balancing and promotion

- `balance.selection_policy` still defaults to `priority`. Opt into
  `least_loaded` to rank eligible workers by active shared-resource request
  count before target priority. It does not migrate a running request or
  replace the separate promotion policy.
- Promotion now estimates five additional turns by default, each with 8192
  new prompt tokens and 1024 output tokens. It charges cold context migration
  once; `balance.promotion.lookahead_turns: 0` restores current-turn-only
  estimates. These are forecasts, not request limits.
- `expected_current_completion_tokens` defaults to 1024 independently of the
  future-turn output estimate. It is bounded by the available output budget
  and never changes upstream `max_tokens`.
- Rate evidence prefers recent measurements, then stable historical samples
  for the same upstream URL/model, then optional `performance_hint` rates.
  Without suitable evidence, KRM stays pinned instead of inventing rates.
  Supply only hints representative of your actual backend.

All policy defaults and tuning options are demonstrated in
[config.example.full.yaml](../config.example.full.yaml). See the
[routing guide](ROUTING_AND_CONCURRENCY.md) for eligibility and cache limitations.

### Dedicated reservations versus shared lanes

Idle reservations protect an entire normalized **upstream URL + model**
resource, not one lane. Keep the default `allow_idle_reservation: true` on
dedicated workers. Set it to `false` on multi-request shared overflow workers:

```yaml
routes:
  pool/shared:
    upstream_url: http://127.0.0.1:8080
    model: your-model
    max_concurrent: 4
    allow_idle_reservation: false
```

Every alias of that resource must resolve the same setting. Source selectors
can keep `balance.idle_reservation.mode: session` for dedicated GPUs; the
shared target vetoes exclusive idle leases while retaining affinity and costed
promotion. Reload removes old leases and prevents in-flight completion
callbacks from recreating them while disabled.

When all slots of a compatible shared target are full, a positive source
`queue_timeout` lets requests enter its real admission queue with source
priority/aging/deadline ordering. This queue remains bound to that resource,
not a continuously re-evaluated global pool. No active generation is preempted.

### Graceful stop and restart

`./krm stop` and `./krm restart` now wait up to **60 seconds** by default and
do not implicitly escalate to SIGKILL. If draining times out, the command fails,
retains the PID and does not launch a replacement process. Wait for the old
process to exit before starting again.

Use `--stop-timeout 120` (or `KRM_STOP_TIMEOUT=120`) for a longer wait; zero
means unlimited. Only explicit `--force` may kill a verified KRM process.
Do not use forced shutdown as the normal upgrade path.

### Operations and access

`GET /models` is an alias of `/v1/models`, with the same network/API-key
filtering. Other OpenAI endpoints keep their `/v1` paths.

BASIC logs now show queue entry, waited admission and compact promotion
decisions. DEBUG adds actual transport phases; `waiting_response_headers`
does not prove that the backend has consumed the prompt or begun prefill.
`/routes` exposes the same lifecycle and effective reservation flags.

Route API keys do not protect `/routes` or `/metrics`; these enforce the
global `client_access` network policy, whose default is `any`. Keep operational data private
with explicit network policy or reverse-proxy/firewall restrictions.

### Deployment checklist

1. Back up local configuration and prompt files. Inspect tracked changes and
   untracked path collisions before updating the source checkout.
2. Update to the intended released version without overwriting local data;
   review the output-budget and routing settings above.
3. If dependencies changed, update the environment using the documented
   [setup](GETTING_STARTED.md) or requirements entry point; check `pip check`.
4. Validate your configuration, then restart gracefully for runtime updates.
   Route hot reload does not load new Python code or recreate startup consumers.
5. Verify `/health`, discovery, expected `/routes` projections and a small
   representative request before resuming normal traffic.
