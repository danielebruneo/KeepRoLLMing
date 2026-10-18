# HTTP API reference

KRM exposes an OpenAI-compatible inference API plus operational endpoints. The
default local address used in examples is `http://127.0.0.1:8000`; add `/v1` to
the base URL for OpenAI SDK clients.

## Authentication

Client credentials are configured per route with `api_keys` and sent as
`Authorization: Bearer <key>`. Routes without configured keys do not require a
credential. A client key is checked against the requested route and is never
forwarded upstream. See [Client API keys](CONFIGURATION.md#client-api-keys).

`/health`, `/metrics`, and `/routes` do not have built-in authentication. In
particular, `/routes` can reveal backend addresses, model names, errors, and
in-flight activity. Keep these operational endpoints on a trusted network or
protect them at the firewall/reverse proxy; do not expose them publicly as-is.

## Inference endpoints

### `POST /v1/chat/completions`

Accepts an OpenAI-compatible chat-completions JSON request. The `model` field
selects a configured KRM route (for example, `chat/local`), not necessarily the
upstream model identifier. The route controls upstream selection, credentials,
filters, context management, and inference overrides.

```json
{
  "model": "chat/local",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": false
}
```

With `stream: false`, KRM returns a JSON chat-completion response. With
`stream: true`, it returns Server-Sent Events (`text/event-stream`) and ends
with the OpenAI-compatible terminal `[DONE]` marker. For safe proxying, reverse
proxies must not buffer the stream; see [Deployment](DEPLOYMENT.md#reverse-proxy).

Known error statuses include `400` for invalid JSON or client request errors,
`401` for a missing/invalid route API key, `413` when the configured request
body safety limit is exceeded, and upstream/routing errors such as `429`,
`502`, `503`, or `504`. An upstream HTTP `400` is returned to the client and
does not trigger route fallback. Streaming errors are represented as terminal
SSE error events where the connection is still writable.

### `POST /v1/embeddings`

Accepts an embeddings request with a route in `model` and `input` as a string
or array of strings. KRM selects the route's upstream URL, applies its upstream
authentication headers, and returns the upstream JSON response.

```json
{
  "model": "embeddings/local",
  "input": ["first sentence", "second sentence"]
}
```

This endpoint currently has a narrower execution path than chat completions:
it does not run chat filters or the chat balance/fallback execution pipeline.
The request body is forwarded to the embeddings upstream, so configure the
client's `model` value to one accepted by that backend. Route API keys are
checked in the same way as for chat. Connection failures return `502`, upstream
timeouts return `504`, and other processing errors return `500`.

## Discovery and operations

### `GET /v1/models`

Returns `{"data": [...]}` using the OpenAI list-models shape. Each item
advertises a public KRM route, not necessarily a backend model:

```json
{
  "id": "chat/local",
  "object": "model",
  "context_length": 32768,
  "owned_by": "orchestrator",
  "config": {
    "upstream_model": "qwen-model",
    "max_tokens": 4096,
    "summary_enabled": true,
    "passthrough_enabled": false,
    "pattern": "chat/local"
  }
}
```

Private routes and `base/` templates are not listed. When a route has
`api_keys`, only callers presenting an accepted Bearer key see it in this list.
The endpoint is a discovery aid; a listed route can still be busy or have an
unhealthy upstream.

### `GET /health`

Returns `{"status":"ok"}` when the KRM process can answer. This is a process
liveness check, not a probe of every configured upstream.

### `GET /metrics`

Returns process-local aggregate accounting as JSON, including request count,
average request time, token totals by model, summary-cache counters, upstream
attempts, recoveries, retry amplification, and usage completeness. It is not a
Prometheus-format endpoint and does not replace the per-request performance
records/dashboard.

### `GET /routes`

Returns a dashboard-oriented snapshot for configured public routes. The top
level contains `generated_at`, `window_minutes` (60), and `routes`. Each route
includes its resolved upstream/model information (or a balance target
description), capabilities, context/output limits, circuit-breaker and
admission state, plus:

- `activity`: 60 one-minute request-count buckets in UTC;
- `errors`: recent route errors retained in the in-memory 60-minute window;
- `performance`: sample count and average prompt TPS, completion TPS, TTFT, and
  elapsed request time;
- `queued_requests`, `pending_requests`, and `active_requests`;
- `balance`: recent selection summary when the route is a balance selector.

Only public routes are returned. State is process-local and in-flight requests
cannot be reconstructed after a process restart; recent completed performance
records may seed activity at startup. This endpoint is operational data, not a
stable replacement for the configuration file.

See [Routing, Balancing & Concurrency](ROUTING_AND_CONCURRENCY.md) for the
selection model and [Configuration](CONFIGURATION.md) for the settings behind
these endpoints.
