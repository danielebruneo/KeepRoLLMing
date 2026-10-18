# Client integration

KRM speaks the OpenAI chat-completions protocol. Configure the client with
KRM's `/v1` base URL and use a **KRM route name** as the model. The route name
stays stable when its backend changes; see [Routes in practice](../README.md#routes-in-practice).

## OpenAI Python SDK

Install the SDK in the client application's environment:

```bash
python -m pip install openai
```

Non-streaming example:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    # Use an accepted KRM route key if the route requires api_keys.
    # Otherwise the SDK still expects a value; KRM ignores it on open routes.
    api_key="your-krm-route-key-or-local-placeholder",
)

response = client.chat.completions.create(
    model="chat/local",
    messages=[{"role": "user", "content": "Hello from the SDK"}],
)
print(response.choices[0].message.content)
```

Streaming example:

```python
stream = client.chat.completions.create(
    model="chat/local",
    messages=[{"role": "user", "content": "Count from one to five"}],
    stream=True,
)
for chunk in stream:
    if not chunk.choices:
        continue
    text = chunk.choices[0].delta.content
    if text:
        print(text, end="", flush=True)
print()
```

Reasoning and tool-call fields are protocol data, not ordinary text deltas.
Clients that use those features should consume the corresponding structured
delta fields and terminal `finish_reason`. See the [API reference](API_REFERENCE.md#inference-endpoints)
for streaming and error behavior.

## LibreChat

Add KRM as an OpenAI-compatible/custom endpoint in the LibreChat configuration
or endpoint settings:

- Base URL: `http://<KRM-host>:8000/v1` (use the address reachable from the
  LibreChat server/container, not necessarily `127.0.0.1`)
- API key: an accepted KRM route key when `api_keys` is configured; otherwise a
  placeholder if the client requires a non-empty value
- Model: the public route name, such as `chat/local` or `code/assistant`

Keep the route name in LibreChat's model list exactly aligned with its KRM
configuration. If LibreChat runs in a container or another VM, verify network
reachability from that environment and do not expose KRM's unauthenticated
operational endpoints to it unnecessarily.

## Embeddings

Use `POST /v1/embeddings` directly for embeddings clients. Unlike chat, the
current embeddings path forwards the request body to the selected upstream;
use a model value accepted by that embeddings backend. See the [API reference](API_REFERENCE.md#post-v1embeddings).
