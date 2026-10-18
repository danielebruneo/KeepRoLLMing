# Troubleshooting

## Setup or import fails

Use the project setup script and confirm the active Python version:

```bash
bash scripts/setup.sh --dev
.venv/bin/python --version
.venv/bin/python -c 'import yaml, keeprollming; print(keeprollming.__version__)'
```

KeepRoLLMing requires Python 3.11 or newer. If a preferred interpreter is not
the default `python3`, run `KRM_PYTHON=python3.12 bash scripts/setup.sh`.

## No matching route

The client's `model` must match a route `pattern`.

```yaml
routes:
  chat/local:
    pattern: "chat/local"
```

```json
{"model":"chat/local","messages":[{"role":"user","content":"Hello"}]}
```

## Configuration fails at startup

Use only the direct route-level mapping:

```yaml
filters:
  model_nudge:
    enabled: true
    max_attempts: 3
```

`filter_chain`, manual `order` lists, and `max_nudge_attempts` are obsolete.
Compare the route against `config.example.full.yaml`.

## Fake quick start fails

Ensure ports 8000 and 19997 are free, or choose alternatives:

```bash
bash scripts/start-with-fake.sh --port 18000 --fake-port 19997
```

Then call `model: "internal/fake"` on the selected proxy port.

## Inspect streaming or tool behaviour

The PLAIN projector is the normal operator view. Temporarily raise its level to
`DEBUG` or `TRACE` in `observability.projectors.plain`. For a bounded raw SSE
capture, enable `raw_trace` only for `selected_routes`. Redact configuration,
requests, and logs before reporting an issue.

## Follow logs for an original route or resolved backend

The log viewer reads the JSONL **logger projection**, not the PLAIN startup
logs or raw network captures. Use the path configured under
`observability.projectors.json.path` (`keeprollming.log.json` in the full example):

```bash
# New events for one original route, including messages, tools and metrics
.venv/bin/python scripts/log-viewer.py --file keeprollming.log.json \
  --tail --route public/pawel-senior

# One request ID (can also be combined with route/backend/event filters)
.venv/bin/python scripts/log-viewer.py --file keeprollming.log.json \
  --tail --req-id a1b2c3d4

# Requests that selected a concrete target route or upstream URL/model
.venv/bin/python scripts/log-viewer.py --file keeprollming.log.json \
  --tail --backend 'pool/swift-*'
.venv/bin/python scripts/log-viewer.py --file keeprollming.log.json \
  --tail --backend '*:7901*'

# Combine original-route/backend selection with event-type selection
.venv/bin/python scripts/log-viewer.py --file keeprollming.log.json \
  --route 'bot/*' --backend 'pool/swift-*' --filter-event 'filter.*'
```

Without `--tail`, the viewer renders the existing file and exits. With
`--tail`, it scans existing records only to recover request identity, then
prints new events and follows file rotation/truncation. Metadata from rotated
files predating startup is not loaded automatically.

`--route` matches the original requested route or client `model`, not the
selected balance/fallback target. `--backend` matches a concrete selected route,
upstream model, or upstream URL (including its base URL without the chat
completion endpoint). Backend selection applies to the **request**, so it keeps
later fallback events too; merely configured alternatives do not match.
`--filter-route` and `--filter-backend` are aliases.
`--req-id` (alias `--filter-req-id`) selects request IDs directly, even when
route/backend metadata is absent. All filters accept
case-sensitive shell globs; quote them to prevent shell expansion. Repeating
one option means OR; different option types combine with AND.

Events are correlated by `req_id`, including multiline content and early events
before resolution. Early events can appear after a short lookup delay, with
their original timestamps; cross-request output is not re-sorted. Correlation
keeps the most recent 10,000 requests and buffers up to 2,048 unmatched events
or 16 MiB. If a limit is reached, a warning explains that earlier unmatched
events may be omitted. Events without request identity are excluded when a
route/backend filter is active. Event-only viewing still includes system events.

Use `--plain-output output.log` to save the rendered view. Relative input paths
are resolved under `LOG_PATH` when set. Malformed records are skipped with a
warning. The viewer cannot reconstruct content omitted/redacted by the JSON
projector: increase its configured level if necessary.

## Reasoning is present but the assistant answer is empty

A successful upstream response may contain only `reasoning_content` (or
`reasoning`) and no `content`, even with `finish_reason: stop`. Check upstream
and downstream traces before assuming KRM dropped a response. A model can
terminate before closing its thinking block or before producing its final
answer. Removing a textual "nothink" instruction does not necessarily disable
the backend's thinking mode; use that runtime's documented control.

KRM forwards prior assistant reasoning when the client includes it in the next
request. It does **not** maintain an independent conversation store or restore
reasoning that the client omitted. Inference behaviour depends on the actual
history, system prompt, model and chat template. KRM does not automatically
publish reasoning as final answer text merely because `content` is empty.

## A tool reports a missing parameter that appears in the log

Inspect the parsed top-level arguments, not just matching text. For example,
`{"raw": "{\"path\": ...}"}` has a `raw` property, not a `path` property.
The string may contain an incomplete JSON object after a token-limit stop.
Check `finish_reason: length` and upstream completion usage in the preceding
request that emitted the call. Avoid "repairing" a truncated file-write call
by executing only its partial content; split large writes in the client.
