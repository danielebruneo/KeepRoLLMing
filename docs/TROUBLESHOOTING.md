# Troubleshooting

## Orchestrator won't start

**Check**: Does `config.yaml` exist? Is `UPSTREAM_BASE_URL` set if no route-level URL?

```bash
CONFIG_FILE=config.yaml python keeprollming.py --port 8000
```

**Check**: Port already in use?

```bash
lsof -i :8000
```

## Nudge not triggering

The model response must end with the trigger pattern (default: `:$`). Check:
1. The response actually ends with `:` (not `.` or `!`)
2. `trigger_patterns` in `model_nudge` config matches
3. `max_nudge_attempts` is not 0

## Tool loop not detected

ToolLoopStopper matches by function name + canonical argument order.
- JSON argument keys are sorted alphabetically for comparison
- Non-JSON arguments (numbers, strings) are compared directly
- `max_repeats` consecutive calls required (default: 3)

## Streaming hangs

Keepalive is emitted every 15s automatically. If the client disconnects:
1. Check `proxy_read_timeout` in nginx/reverse proxy
2. Check network firewall idle timeout
3. Verify the upstream LLM is responding

## "No matching route"

The `model` parameter in your request must match a `pattern` in the config:

```bash
curl -s ... -d '{"model":"my-model",...}'
```

Must match a route like:
```yaml
routes:
  my-route:
    pattern: "my-model"
```

Patterns support glob: `code/*` matches `code/review`, `code/debug`, etc.

## Logs

```bash
LOG_MODE=BASIC_PLAIN python keeprollming.py --port 8000
```

Shows structured blocks with request ID, route, model, filter events, and timing.

## Getting Help

Open an issue on GitHub with:
- Config (redacted)
- Request that caused the issue
- Log output (BASIC_PLAIN mode)
- Python version and OS
