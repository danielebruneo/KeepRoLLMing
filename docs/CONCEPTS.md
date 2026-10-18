# Core concepts

| Term | Meaning |
|---|---|
| **Route** | A client-facing name (sent in the request's `model`) that maps to upstream settings and KRM behavior. |
| **Pattern** | The exact name or pattern a route uses to match the client's `model` value. |
| **Private/base route** | A configuration template marked private; other routes can inherit it, but clients do not see it in `/v1/models`. |
| **`extends`** | Configuration inheritance. Child values override inherited settings; it does not send a request to the parent route. |
| **Filter** | A configured module that can alter request/response handling, for example injecting a system prompt or rewriting supported tool-call formats. |
| **Fallback** | An ordered route attempt used after an eligible upstream failure. A streaming request cannot safely switch upstream after client-visible output has started. |
| **Reroute / balance selector** | A route that resolves a request to another concrete route at runtime. The destination owns its own transport and execution settings; see the [routing guide](ROUTING_AND_CONCURRENCY.md). |
| **Backend resource** | The effective `(upstream_url, model)` pair. Routes sharing that pair share capacity and circuit-breaker state. |
| **Admission control** | Capacity and queue policy that determines whether a request may use a backend resource now, must wait, or must be rejected. |
| **Reservation / affinity** | A time-bounded preference or lease intended to keep a session on a warm backend. It never interrupts an active request. |
| **Circuit breaker** | A failure-state guard that temporarily stops sending work to an unhealthy backend and later permits a recovery probe. |
| **SSE stream** | The event stream used by `stream: true`. KRM forwards structured content deltas and terminal events; reverse proxies must not buffer it. |

For end-user endpoint behavior, see the [API reference](API_REFERENCE.md). For
configuration syntax, see [Configuration](CONFIGURATION.md).
