# Filter pipeline

KeepRoLLMing filter modules are configured per route under `filters:`. Each
module owns its configuration schema and may contribute to one or both phases:

1. **Request phase:** filters inspect or transform the outgoing request.
2. **Streaming phase:** eligible modules create stream finalizers that observe
   canonical events, preserve protocol ordering, and may request recovery.

The canonical registry is `keeprollming.filters.registry`. It validates module
names and configuration at startup; third-party registration is not yet a
stable public API.

## Built-in modules

| Module | Request phase | Streaming phase | Purpose |
|---|---|---|---|
| `system_prompt` | yes | no | Inject or replace a system prompt |
| `summarization` | yes | no | Repack under context pressure |
| `multimodal_validator` | yes | no | Repair image marker/payload mismatches |
| `tool_rewrite` | yes | yes | Convert supported XML pseudo-tool calls |
| `timestamp` | yes | yes | Add request/response time markers |
| `model_nudge` | yes | yes | Continue lazy/incomplete output |
| `model_tool_loop_stopper` | yes | yes | Stop repeated tool calls |
| `reasoning_loop_stopper` | yes | yes | Stop repeated reasoning |

Modules are sorted by their declared priority in each phase. A route may set
`priority` only when it needs an explicit override; configuration mapping order
does not control execution order.

## Streaming contract

Streaming finalizers see the same canonical stream events in priority order.
They may buffer output internally, but final `finish_reason` and `[DONE]` stay
behind the terminal decision barrier. This preserves compatibility with strict
OpenAI clients while allowing nudge and loop-recovery behaviour.

For concrete settings, use the individual routes in
[`config.example.full.yaml`](../config.example.full.yaml). They intentionally
separate modules whose defaults would otherwise collide, so each example is
valid on its own.
