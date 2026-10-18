<p align="center">
  <img src="assets/keeprollming-header.png" alt="KeepRoLLMing" width="600">
</p>

<p align="center">
  <strong><em>Keep your LLM rolling.</em></strong><br>
  Smart routing and reliable AI agents across local GPUs and remote LLM backends.
</p>

<p align="center">
  <a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL_v3-blue.svg" alt="AGPL v3 license"></a>
  <img src="https://img.shields.io/badge/Status-Alpha-orange" alt="Alpha status">
</p>

# KeepRoLLMing

KeepRoLLMing is an OpenAI-compatible LLM gateway for routing, sharing, and
protecting access to local and remote backends. Give clients stable model
names, distribute work across your GPU servers with configurable priorities,
and keep conversations on warm backends with sticky sessions.

It also keeps long-running chats and agents moving with context management,
continuation recovery, tool/reasoning loop protection, request filters, and
observable streaming—all configured in YAML, without changing your clients.

> **Alpha software.** Configuration and extension APIs may evolve before 1.0.
> Requests and responses can be logged locally. Review `observability` settings
> before using sensitive traffic or exposing the service to untrusted networks.
> Features marked **unreleased** in `CHANGELOG.md` are not part of the latest
> published tag yet.

## The problem

Running several LLM backends is more than choosing a model. Your coding agent,
background bots, and shared users may compete for the same GPUs. Moving a
conversation between workers can lose a useful prefix cache, while exposing
the same service outside your LAN needs a different access policy.

Models also fail in recurring, recognizable ways during long chats and agent
loops. KeepRoLLMing handles both resource coordination and model reliability
between OpenAI-compatible clients and their backends.

| Failure mode | Typical symptom | KeepRoLLMing capability |
|---|---|---|
| Shared GPU contention | Background or shared traffic competes with interactive work | Prioritized routing, per-backend capacity, and bounded queues |
| Conversation hopping | Agent turns repeatedly move to cold workers | Sticky sessions and configurable idle reservations |
| Mixed private/public serving | LAN clients need convenient access; external users need credentials | Per-route network policies, API keys, and trusted-proxy handling |
| Incomplete response | The model ends after “Let me check:” | `model_nudge` can request continuation |
| Repeated tool call | The same call is made again with the same arguments | `model_tool_loop_stopper` can interrupt the loop |
| Repeated reasoning | The model reproduces the same reasoning block | `reasoning_loop_stopper` can interrupt the loop |
| Context pressure | A long conversation approaches the model window | `summarization` can repack prior context |
| Non-standard tool output | An upstream model emits supported XML pseudo-calls | `tool_rewrite` converts them to OpenAI tool calls |
| Slow streaming | A client needs an OpenAI-compatible, observable stream | The canonical streaming pipeline preserves SSE ordering and terminal framing |

## The solution

KeepRoLLMing sits between your client and any OpenAI-compatible backend. Your
client continues to call `/v1/chat/completions`; routes select the backend and
enable only the modules appropriate for that route.

```text
OpenAI-compatible clients → routes + access policies → backend selection
                                                     → filters → LLM backends
```

The practical result is longer-running conversations and agents with less
manual recovery: a client can keep a stable model name while you change an
upstream, tune inference parameters, add a system prompt, or enable protection
against a specific failure mode in configuration.

## Built for local and multi-GPU setups

Run your model servers on different GPUs or machines, then put KRM in front of
them. KRM routes requests to those existing servers; it does not load models
or allocate GPU memory itself.

For example, prioritize your coding routes over shared public traffic and
background bots. Prefer faster workers for interactive work, let other traffic
use spare capacity, and configure different backend preferences for each pool.

**Sticky sessions** use conversation-prefix affinity to prefer a backend that
may still have the context cached, without requiring clients to send a session
ID. Optional idle reservations protect that warm backend between agent turns
and tool calls. At a new request boundary, a session can move to a better
available worker when the estimated benefit justifies rebuilding its cache.

**Configurable balancing and admission policies** combine target preferences,
cross-route request priorities, capacity limits, queue waiting, reservation
timeouts, and aging. Tune them per workload rather than treating every request
as an interchangeable round-robin job. Decisions respect backend health and
context limits, with optional enforcement of declared capabilities. Active
generations are never interrupted just to give another request priority.

See [Routing, balancing & concurrency](docs/ROUTING_AND_CONCURRENCY.md) for
the deterministic selection rules, affinity heuristics, and policy settings.

## What you get

- **Backend abstraction:** stable client-facing routes for llama.cpp, vLLM, LM
  Studio, remote OpenAI-compatible APIs, and similar servers.
- **Prioritized multi-GPU routing:** balance requests across backend pools,
  coordinate shared capacity across routes, and give interactive work precedence
  over background or shared workloads.
- **Cache-friendly sticky sessions:** prefix affinity, configurable reservations,
  compaction handoff, and request-boundary promotion reduce avoidable worker hops.
- **Mixed LAN/public access:** source-IP/CIDR policies and per-route client API
  keys let local tools stay keyless while external routes require credentials;
  forwarded client IPs are honored only through configured trusted proxies.
- **Composable reliability modules:** request, response, and streaming modules
  for context handling, recovery, tool calls, and reasoning.
- **OpenAI-compatible streaming:** canonical events preserve the ordering of
  reasoning, tool calls, terminal `finish_reason`, and `[DONE]`; eligible
  structured tool calls stream as they arrive without weakening loop safety.
- **Route resilience controls:** opt-in admission limits and circuit breakers
  make capacity and upstream failure behavior explicit per route.
- **Local observability:** configurable PLAIN/JSON projections, performance
  metrics, and opt-in raw SSE capture for focused debugging; privacy mode can
  retain operational facts without persisting request or model content.
- **Per-route configuration:** inheritance keeps shared backend settings in
  one place while each client-facing route stays explicit.

## Quick start

Requires Python 3.11 or newer.

```bash
git clone https://github.com/danielebruneo/KeepRoLLMing.git
cd KeepRoLLMing
bash scripts/setup.sh
bash scripts/start-with-fake.sh
```

In another terminal, while the fake server is running:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"internal/fake","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

The fake backend is a deterministic smoke test. Stop it with `Ctrl-C` in the
terminal that runs `start-with-fake.sh`.

## Connect an LLM backend

```bash
cp config.example.yaml config.yaml
# Edit config.yaml: set base/local.model and base/local.upstream_url.
./krm serve --port 8000 --config config.yaml
```

Send requests using the configured route name, for example `chat/local` or
`code/assistant`. Routes can point to llama.cpp, vLLM, LM Studio, or another
OpenAI-compatible API. See [Configuration](docs/CONFIGURATION.md).

For a local background process with logs in a writable project directory:

```bash
./krm start --port 8000 --config config.yaml
./krm status --port 8000
```

To connect an application, use KRM's OpenAI-compatible base URL and the
client-facing route name as its model. See the copy-paste examples for the
[OpenAI Python SDK and LibreChat](docs/CLIENT_INTEGRATION.md), and the complete
[HTTP API reference](docs/API_REFERENCE.md).

## Routes in practice

Routes are a small configuration layer between clients and backends. A private
base route holds shared connection settings; public routes inherit it and give
clients meaningful, stable model names.

Here `is_private: true` marks an inheritance-only template; it is not a network
access restriction. Use `client_access` and `api_keys` to protect serving routes.

```yaml
routes:
  base/local:
    is_private: true
    model: "qwen-local"
    upstream_url: "http://127.0.0.1:8080"

  chat/default:
    extends: base/local
    pattern: "chat/default"

  chat/creative:
    extends: base/local
    pattern: "chat/creative"
    overrides:
      temperature: 0.8
      top_p: 0.95
      max_tokens: 2048
      reasoning_effort: high
```

Clients use `chat/default` or `chat/creative`; the configured `overrides` are
applied to the request sent upstream. You can move either route to another
backend later without changing client configuration.

`reasoning_effort` is forwarded as the OpenAI-compatible top-level request
field used by LibreChat's reasoning control. Its supported values are defined
by the selected upstream model/runtime; for example, a Qwen-compatible backend
may accept `low`, `medium`, or `high`.

Routes can also fail over to another route after an upstream failure:

```yaml
routes:
  base/remote:
    is_private: true
    model: "remote-chat-model"
    upstream_url: "https://api.example.com"
    api_key: "replace-me"

  chat/remote:
    extends: base/remote
    pattern: "chat/remote"

  chat/resilient:
    extends: base/local
    pattern: "chat/resilient"
    fallback_chain: ["chat/remote"]
```

Here a request to `chat/resilient` uses the local backend first, then tries
`chat/remote` only if the upstream attempt fails. Inheritance also composes
filter settings, so route-specific behaviour remains explicit rather than
being embedded in each client. See the complete, validated
[`config.example.full.yaml`](config.example.full.yaml) for all route fields and
built-in filters.

For multiple local workers, a route can select from concrete backend routes
without exposing that topology to clients:

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
    balance:
      targets:
        - {route: code/runner-a, priority: 100}
        - {route: code/runner-b, priority: 80}
```

KRM uses bounded session affinity to prefer an eligible backend already warm
for a conversation, while respecting each target route's capacity. See
[Configuration](docs/CONFIGURATION.md#smart-routing-with-balance) for the
configuration contract and [Routing, Balancing & Concurrency](docs/ROUTING_AND_CONCURRENCY.md)
for the selection, reservation, queueing, and fallback rules.

## How it works

```text
OpenAI client → route resolution + access checks → selection + admission
              → request filters → upstream backend
              ← canonical streaming pipeline ← stream finalizers
```

Routes are declared in `config.yaml`. Each route may inherit settings from a
base route and enables filter modules under a direct `filters:` mapping. YAML
mapping order is not significant: each module declares its own priority, which
can be explicitly overridden per route when required.

Built-in modules include:

- `system_prompt` — inject or replace a system prompt.
- `summarization` — repack a conversation when context pressure requires it.
- `multimodal_validator` — repair image marker/payload mismatches.
- `tool_rewrite` — convert supported XML pseudo-tool calls to OpenAI calls.
- `model_nudge` — continue a response that ends in a configured lazy pattern.
- `model_tool_loop_stopper` and `reasoning_loop_stopper` — interrupt repeated behaviour.
- `timestamp` — inject request time and append a response timestamp.

## Configuration examples

- [`config.example.yaml`](config.example.yaml): minimal configuration to copy
  and edit.
- [`config.example.full.yaml`](config.example.full.yaml): validated reference
  for all public settings and built-in filter options.

Do not copy placeholder API keys or URLs from the full example.

### Route access and status

Keep personal routes LAN-only without reconfiguring your existing clients,
while exposing selected routes with API keys:

```yaml
client_access:
  default: lan
  lan_networks: ["127.0.0.0/8", "::1/128", "192.168.1.0/24"]  # Use your actual LAN.
  trusted_proxies: []  # Add only the exact addresses of reverse proxies you control.

routes:
  chat/personal:
    model: "local-model"
    upstream_url: "http://127.0.0.1:8080"

  public/chat:
    extends: chat/personal
    client_access: public
    api_keys: ["replace-with-a-client-key"]
```

`chat/personal` accepts LAN callers without a key; `public/chat` accepts callers
from any network only with an accepted key. This is an explicit configuration,
not the default network policy. Route names such as `public/` are labels, not
security rules. Behind a reverse proxy, configure its trusted address so KRM
can evaluate the original client IP safely.

Routes can be protected with one or more client API keys declared in YAML. Keys
are inherited through route composition, so a private base route can secure a
family of public-facing models while selected routes can opt out explicitly.
KRM accepts the standard `Authorization: Bearer <key>` header and never forwards
that client credential to an upstream backend.

For trusted operational networks, `GET /routes` exposes a read-only status
snapshot of public routes: resolved backend details, declared capabilities,
rolling activity/errors/performance, and requests currently in flight. See
[Configuration](docs/CONFIGURATION.md#client-api-keys) for API-key setup,
[client network policies](docs/CONFIGURATION.md#client-network-access-policies), and
[the route-status endpoint](docs/CONFIGURATION.md#private-route-status-endpoint)
for its security boundary and response contract.

## Documentation

| Guide | Purpose |
|---|---|
| [Getting started](docs/GETTING_STARTED.md) | Install, run the fake backend, connect an upstream |
| [Client integration](docs/CLIENT_INTEGRATION.md) | Connect the OpenAI Python SDK or LibreChat |
| [HTTP API reference](docs/API_REFERENCE.md) | Endpoints, authentication, responses, and streaming |
| [Configuration](docs/CONFIGURATION.md) | Root settings, routes, inheritance, observability |
| [Routing, balancing & concurrency](docs/ROUTING_AND_CONCURRENCY.md) | Target selection, affinity, reservations, and admission |
| [Concepts](docs/CONCEPTS.md) | Short glossary of routes, filters, and runtime controls |
| [Filter pipeline](docs/FILTER_PIPELINE.md) | Module lifecycle and streaming behaviour |
| [Deployment](docs/DEPLOYMENT.md) | Daemon, container, reverse proxy |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Startup, routing, logging, streaming diagnosis |
| [Contributing](CONTRIBUTING.md) | Development setup and verification loop |

## Contributing

```bash
bash scripts/setup.sh --dev
bash scripts/run-single-test.sh tests/filters/test_registry.py
bash scripts/run-parallel-tests.sh
.venv/bin/ruff check keeprollming tests
.venv/bin/ruff format --check keeprollming tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the public contribution workflow.

## License

AGPL-3.0-only. See [LICENSE](LICENSE).
