<p align="center">
  <img src="assets/keeprollming-header.png" alt="KeepRoLLMing" width="600">
</p>

<p align="center">
  <b style="font-size:1.2em;font-style:italic">Keep your LLM rolling</b><br>
all the tools you need to achieve long running AI Chats or Agents on your Local LLM.
</p>

<p align="center">
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/Status-Alpha-orange" alt="Status: Alpha">
</p>

**OpenAI-compatible proxy that makes LLMs more reliable** — auto-retry for lazy responses, tool loop detection, reasoning loop detection, context window management, and SSE keepalive to prevent client timeouts on slow local LLMs.

> **⚠️ Privacy Warning:** By default, KeepRoLLMing logs request and response content locally for observability. Do not use it with sensitive traffic or expose it to untrusted networks unless you have reviewed and adjusted the logging/privacy settings (`config.yaml` → log level, log path, rotation). See [Privacy & Data Handling](#privacy--data-handling) below.

## The Problem

LLMs are powerful but unreliable in predictable ways:

| Problem | Example | Solution |
|---------|---------|----------|
| **Lazy response** | Model outputs `"Let me check...:"` and stops | **ModelNudge** — auto-retry with "Continue." |
| **Tool loop** | Model calls `read_file()` with same args 7 times | **ToolLoopStopper** — detect and block |
| **Reasoning loop** | Model repeats same thinking block verbatim | **ReasoningLoopStopper** — detect and retry |
| **Context overflow** | 128k window fills up mid-conversation | **Summarization** — rolling cache-append |
| **XML tool calls** | Some backends output `<tool_call><function=name>...</>` | **ToolRewrite** — convert to OpenAI format |
| **Client timeout** | Local LLM takes minutes, client gives up | **SSE Keepalive** — heartbeat every 15s |

## The Solution

KeepRoLLMing sits **between your client and any OpenAI-compatible backend**, intercepting every request and response. Instead of passing messages through unchanged, it applies a **filter pipeline** — a sequence of pluggable modules that can inspect, modify, or retry requests and responses automatically.

Each filter targets a specific failure mode:
- **ModelNudge** detects when the model stops mid-response (trailing `:`) and auto-retries with a continuation prompt.
- **ToolLoopStopper** and **ReasoningLoopStopper** detect repetitive behavior (same tool call N times, same thinking block verbatim) and break the loop.
- **Summarization** condenses older conversation turns so the context window never overflows.
- **ToolRewrite** converts XML tool calls (from backends that don't support structured tool calls) into the standard OpenAI format.
- **SystemPrompt** injects or overrides system messages without modifying client code.
- **Timestamp** injects UTC timestamps into requests and responses. This gives the model temporal awareness — it knows when each message was sent, which helps maintain coherence across long contexts and makes agentic steps traceable. Emerging evidence suggests timestamp markers also improve the model's ability to order information chronologically.
- **SSE Keepalive** sends `: keepalive\n\n` every 15 seconds during long streaming responses. This prevents client-side timeouts on slow local LLMs (which may take minutes to generate) by keeping the connection alive and proving the server is still processing.

The result: your LLM keeps rolling — longer conversations, fewer loops, less manual retrying.

## Project Status

> **Released:** 2026-06-27 — **Version:** 0.9.1

KeepRoLLMing is **alpha-quality software**. It has been testing and evolving for months on real-world scenarios, but it is still early in its public life. APIs may change, configuration formats may be refined, and new filters will be added as the project matures toward a stable 1.0.0 release.

Bug reports, feature requests, and contributions are welcome.

## Key Features

### Model Abstraction

KeepRoLLMing decouples your client from your LLM backends with a **route layer**:

```
Client → keeprollming → upstream LLM
         (routes)
```

Instead of pointing your client directly at a model URL, you define **named routes** in config:

```yaml
routes:
  chat/default:   {model: "fast-model",     upstream_url: "http://llm1:8080"}
  chat/deep:      {model: "slow-powerful",  upstream_url: "http://llm2:8080"}
  code/review:    {model: "code-specialist", upstream_url: "http://llm3:8080"}
```

Your client calls `model: "chat/default"` — if you want to switch backend, change the config file. The orchestrator supports **hot reload**: edit `config.yaml` and the next request picks up the changes immediately, no restart needed.

### Parameter Override

Many LLM clients don't expose advanced inference parameters. KeepRoLLMing lets you set them per-route:

```yaml
routes:
  chat/creative:
    pattern: "chat/creative"
    upstream_url: "http://llm:8080"
    overrides:            # ← override upstream inference params
      temperature: 0.42
      top_p: 0.95
      top_k: 45
      min_p: 0.01
      presence_penalty: 0.05
      repetition_penalty: 1.05
      temperature: 0.8
      frequency_penalty: 0.3
      max_tokens: 4096
```

The orchestrator merges these into the upstream payload, even if your client doesn't support setting them.

### Remote API Authentication

Use `api_key` to authenticate against remote OpenAI-compatible providers (DeepSeek, OpenRouter, etc.):

```yaml
routes:
  remote/deepseek:
    model: "deepseek-chat"
    upstream_url: "https://api.deepseek.com"   # /v1/chat/completions auto-appended
    api_key: "sk-your-api-key"                 # → Authorization: Bearer sk-your-api-key
```

The `api_key` field automatically:
- Adds `Authorization: Bearer {key}` to every upstream call
- Passes the key to filters (nudge, TLS, RLS) for their retry HTTP calls

For custom headers beyond Bearer token, use `upstream_headers`:

```yaml
    upstream_headers:
      X-API-Key: "custom-value"
      X-Custom-Header: "value"
```

### System Prompt Manipulation

Use the `SystemPrompt` filter to inject, override, or toggle system prompts without modifying client code:

```yaml
filter_chain:
  order: [system_prompt]
  filters:
    system_prompt:
      enabled: true
      prompt: "/nothink"          # suppress reasoning
      override: false
```

Combine with route inheritance to build an **agentic harness**:

```yaml
routes:
  base/agent:         {filter_chain: {order: [system_prompt], filters: {system_prompt: {enabled: true, prompt: "You are a coding agent."}}}}
  code/thinkon:       {extends: base/agent, pattern: "code/thinkon"}
  code/thinkoff:      {extends: base/agent, pattern: "code/thinkoff",
                       filter_chain: {order: [system_prompt], filters: {system_prompt: {enabled: true, prompt: "/nothink"}}}}
```

Your client just picks `code/thinkon` or `code/thinkoff` — no client-side changes needed.

### Rolling Summarization

KeepRoLLMing can automatically summarize older messages when the conversation exceeds a configured context window. This uses a **cache-append** strategy: the original messages stay in the conversation, but a summary is injected before the next turn, keeping the model within its context window.

```yaml
routes:
  chat/long:
    model: "llama3.1-70b"
    upstream_url: "http://llm:8080"
    ctx_len: 32768
    summary_enabled: true    # enable per-route
    summary_model: "qwen3.5-4b"  # lightweight model for summarization
```

The summarization filter runs on the response, not on streaming chunks. Configure which model handles the summarization separately — use a fast local model for summarization even if the main model is a remote API.

### Observability

Every request produces structured log output with end-to-end traceability:

```
[2026-06-24 10:15:23] ── REQ a1b2c3d4 ──────────────────────────
  req_id=a1b2c3d4 | model=chat/default | route=chat/default
  upstream=http://llm:8080 | status=200 | duration=3.2s
  tokens: prompt=142 | completion=89 | total=231
  filters: system_prompt ✓ | model_nudge ✓ (2 retries)
```

Use `LOG_MODE=BASIC_PLAIN` for human-readable format, or the default JSON for machine parsing. Track per-model performance:

### Performance Dashboard

`perf_dashboard.py` provides a real-time terminal UI for monitoring model performance. It watches the performance logs directory and displays:

```

Model           | Requests | Avg T/s | Tokens/s | Errors | Latest

qwen3.5-4b      |      142 |    32.4 |    45.2  |      2 | 12:34:56

llama3.1-70b    |       89 |    12.1 |    18.7  |      0 | 12:35:01

```

```bash

python perf_dashboard.py                         # auto-detect logs dir

python perf_dashboard.py /path/to/performance_logs  # custom path
```

### Log Viewer

The orchestrator produces NDJSON logs (`keeprollming.log.json`). Use `scripts/log-viewer.py` to render them in human-readable BASIC_PLAIN format:

```bash
python scripts/log-viewer.py                      # live tail
python scripts/log-viewer.py --file /path/to/log.json
python scripts/log-viewer.py --plain-output /tmp/plain.log  # save to file
```

```json
{"msg": "request_complete", "req_id": "a1b2c3d4", "model": "chat/default",
 "upstream_model": "fast-model", "duration_ms": 3200, "tokens": 231}
```

## Quick Start

### Test with fake backend

```bash
# 1. Setup (creates .venv and installs dependencies)
bash scripts/setup.sh

# 2. Start with built-in fake backend for testing
bash scripts/start-with-fake.sh

# 3. Try it
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"internal/full","messages":[{"role":"user","content":"Hello"}]}'
```

### Connect your own LLM backend

```bash
# 1. Setup
bash scripts/setup.sh

# 2. Create config.yaml (see docs/CONFIGURATION.md for full reference)
cp config.example.yaml config.yaml
# Edit config.yaml: set upstream_url, routes, etc.

# 3. Start as daemon
bash scripts/daemon.sh start --port 8000
```

## How It Works

```
Client → POST /v1/chat/completions
  → Route Resolution (model → route + extends chain)
  → Filter Pipeline (request)
  → Upstream LLM Call
  → Filter Pipeline (response)
  → Response to client
```

Filters compose in priority order — configure per-route in `config.yaml`. The pipeline is **modular and extensible**: you can write your own filter by subclassing `Filter` (non-streaming) or `StreamingFilterBase` (for streaming-aware filters with buffering and retry) and registering it with `@register_filter("name")`. Custom filters can modify requests, responses, or both.

```python
from keeprollming.orchestrator.filter import Filter, FilterConfig, register_filter

@register_filter("my_custom_filter")
class MyFilter(Filter):
    priority = 42
    def process_request(self, payload, context):
        # Modify request before it reaches upstream
        return payload
    def process_response(self, response, context):
        # Modify response before it reaches the client
        return response
```

Configure per-route in `config.yaml`:

```yaml
routes:
  my-model:
    pattern: "my-model"
    model: "qwen3.5-4b"
    upstream_url: "http://localhost:1234"
    filter_chain:
      order: [system_prompt, tool_loop_stopper, model_nudge]
      filters:
        system_prompt:
          enabled: true
          prompt: "/nothink"
        tool_loop_stopper:
          enabled: true
          max_repeats: 2
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
```

## Documentation

| Guide | What it covers |
|-------|----------------|
| [Getting Started](docs/GETTING_STARTED.md) | Installation, configuration, first request |
| [Configuration](docs/CONFIGURATION.md) | Full config reference, route system, inheritance |
| [Filter Pipeline](docs/FILTER_PIPELINE.md) | Each filter in detail: when and how to use |
| [Deployment](docs/DEPLOYMENT.md) | Production: daemon, Docker, reverse proxy |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |

## Testing

```bash
bash scripts/set-tests-venv.sh
bash scripts/run-parallel-tests.sh   # Full suite
bash scripts/start-with-fake.sh      # Live test with fake backend
```

## Known Limitations

- **Alpha software.** APIs, configuration formats, and filter behaviour may change without notice as the project stabilises.
- **Tested primarily with local/OpenAI-compatible backends** (llama.cpp, vLLM, DeepSeek API). Compatibility with custom or proprietary backends may vary.
- **No built-in privacy mode.** Request/response content is logged by default. See [Data Privacy](#data-privacy) for mitigation.
- **Open-source community.** This project is maintained in the open but does not guarantee enterprise-level support or SLAs.

## Data Privacy

By default, KeepRoLLMing logs all requests and responses (including message content) to `keeprollming.log.json`. This is essential for debugging, observability, and performance monitoring.

If you need **zero content logging** — for example when processing sensitive data — KeepRoLLMing does not yet support a built-in privacy mode that strips message content from logs while retaining metadata (tokens, timing, model, errors). This feature is on the roadmap.

In the meantime:
- Logs are written **locally** only — no telemetry, no external service
- Logs are **not** sent anywhere unless you explicitly configure log shipping
- Disable logging entirely by setting `LOG_MODE=ERROR` in the environment
- The `perf_dashboard.py` tool reads performance summaries, not raw content

## About the Name

**KeepRoLLMing** = Keep + Roll + **LLM** + ing → "Keep Rolling LLMs".

The name reflects the project's core mission: keeping your language model rolling — never stuck, never looping, never running out of context. The *Ming* in KeepRoll**Ming** comes naturally from pronouncing KeepRo**ll** + LL**M** + **ing** — and happens to evoke a *Ming vase*, which by rolling becomes perfectly round.

## License

AGPL v3 — see [LICENSE](LICENSE).
