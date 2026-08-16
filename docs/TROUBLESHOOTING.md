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
