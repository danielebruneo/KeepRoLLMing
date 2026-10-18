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

When `multimodal_validator.max_images` is set, its default
`max_images_policy: strip_first` removes the oldest excess image parts and
retains the newest images. `strip_latest` is available when retaining the
earliest images is preferable. In either case an omitted image becomes the
configured replacement text rather than altering the message shape.

`multimodal_validator.strip_all_files: true` removes structured file attachments
from **all messages**, including old user/assistant/tool messages, before either
streaming or non-streaming upstream execution. Default: `false`. Each `file`,
`input_file`, `document`, `file_url` or `document_url` content part becomes a
text placeholder; customize `files_replacement_text` (default
`[File omitted: attachments are disabled]`). PDFs mislabeled as images with a
PDF data URL or `source.media_type: application/pdf` are stripped too. Actual
images, extracted document text, tool arguments, message roles and tool IDs
are preserved. The filter does not fetch URLs or guess file types from names,
and does not remove images produced by rendering PDF pages: use the separate
`strip_all_images` switch for that case. A BASIC
`filter.multimodal_validator.files_stripped` event reports only the removed
count, not filenames or contents.

## Loop recovery contract (RLS and TLS)

Both serving modes use the same detection settings and recovery budgets:

| Setting | Meaning | Default |
|---|---|---|
| `max_repeats` | Matching preceding occurrences needed to block the current reasoning/call; `1` blocks the first repeat, `0` disables exact detection | `1` |
| `max_retries` | Maximum upstream recovery calls by this filter in one request; `0` uses the terminal fallback immediately | `2` |
| `max_attempts` | Alias for `max_retries`; an explicit `max_retries` takes precedence | omitted |
| `retry_timeout` | Optional recovery timeout override in seconds; otherwise inherit the resolved route's timeout | omitted |
| `send_user_message` | Append the configured user instruction during intervention | `true` |

RLS compares the preceding reasoning-bearing assistant turns in the current
request. Different tool signatures disambiguate an otherwise identical
reasoning block. There is no process-global cross-client reasoning cache.
If a client omits reasoning from its conversation, cross-turn RLS detection
cannot infer it; TLS can still inspect historical tool calls.

An RLS intervention appends an internal assistant correction
(`intervention_message`) and, when enabled, a user instruction (`rls_message`).
A TLS intervention appends the withheld assistant tool call and its matching
synthetic tool result (`tls_message`), then the optional user instruction
(`nudge_message`). These are internal recovery messages, not actual tool
executions. Every synthetic result has a matching call ID in that retry.

Recovery retains the original tool definitions, inference parameters, resolved
backend, headers/authentication and transport policy. Non-streaming regenerated
responses re-enter response validation (including tool rewrite and both loop
stoppers); request transformations do not run again. Per-filter counters remain
request-scoped across re-entry, with a global ceiling of ten recovery calls.
Streaming preserves the terminal Finish/Done barrier and per-filter budgets.

TLS checks newly produced calls against relevant history, not whether any old
loop exists anywhere in the conversation. `fuzzy_max_repeats` counts identical
signatures in a bounded `fuzzy_look_back` window; `fuzzy_threshold` independently
enables approximate argument matching within the same function. AB detection
considers the current call as the last element of the pattern. Exact streaming
can release provably different calls early; these heuristic modes buffer until
the terminal decision.

If recovery repeats or fails, the filter returns its non-empty fallback with
`finish_reason: stop`. The fallback is terminal, not another automatic user
turn. A configured Nudge may still continue a matching fallback under the
remaining budgets. Actual upstream `length`/`content_filter` outcomes remain
authoritative, including incomplete tool calls, rather than being relabeled
as successful tool execution. Cancellation propagates to the recovery call.

`filter.loop.recovery` events at BASIC report non-streaming retry, received,
failed, cancelled and exhausted outcomes without message contents. Accounting
includes all upstream attempts and keeps rejected work separate from the final
logical response. Streaming recovery decisions and transport events use the
existing stream observability path.

## Streaming contract

Streaming finalizers see the same canonical stream events in priority order.
They may buffer output internally, but final `finish_reason` and `[DONE]` stay
behind the terminal decision barrier. This preserves compatibility with strict
OpenAI clients while allowing nudge and loop-recovery behaviour.

Structured tool-call deltas are relayed as they arrive when no finalizer needs
to hold them. With exact-only `model_tool_loop_stopper`, a call whose function
name cannot match an earlier relevant call is also released early; a
same-name call waits until its complete canonical arguments can be compared.
Fuzzy/AB loop modes and recovery modes that could need to retract a turn keep
their conservative terminal-buffering behaviour. A client must always treat
the terminal `finish_reason` as authoritative.

For concrete settings, use the individual routes in
[`config.example.full.yaml`](../config.example.full.yaml). They intentionally
separate modules whose defaults would otherwise collide, so each example is
valid on its own.
