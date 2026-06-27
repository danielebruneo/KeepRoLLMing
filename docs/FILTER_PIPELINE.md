# Filter Pipeline

Filters run in priority order on every request. They can modify the request before it reaches the upstream, modify the response after, or both.

## Priority Order

| Pri | Filter | Request | Response |
|:---:|--------|:-------:|:--------:|
| 10 | **SystemPrompt** | inject/override prompt | — |
| 15 | **Summarization** | — | rolling summarization |
| 20 | **ToolRewrite** | — | XML → structured tool_calls |
| 20 | **ReasoningLoopStopper** | — | detect reasoning loops |
| 25 | **ToolLoopStopper** | — | detect tool call loops |
| 30 | **MultimodalValidator** | validate vision payloads | — |
| 50 | **ModelNudge** | — | lazy response → retry |
| 100 | **Timestamp** | inject UTC timestamp | append timestamp |

## Detailed Descriptions

### SystemPrompt (p10)

**Role**: Injects or overrides the system prompt in the request payload.

Useful when the upstream backend doesn't support system messages, or when you want to enforce a prompt template per route.

```yaml
filter_chain:
  order: [system_prompt]
  filters:
    system_prompt:
      enabled: true
      prompt: "You are a coding assistant. Be concise."
      override: false  # true = replace existing system prompt
```

### ModelNudge (p50)

**Role**: Detects lazy/incomplete responses (e.g. ending with `:`) and automatically retries with a "Continue." prompt.

This is the core reliability feature. Many models occasionally stop mid-response with a trailing colon. ModelNudge catches this, retries upstream with accumulated context, and returns the full response to the client.

```yaml
filter_chain:
  order: [model_nudge]
  filters:
    model_nudge:
      enabled: true
      trigger_patterns: [":$"]           # regex for lazy detection
      nudge_message: "Continue."          # sent on each retry
      max_nudge_attempts: 3               # stop after N retries
```

Streaming: laziness is detected on `finish_reason`. Buffered content + retry response are accumulated.

### Timestamp (p100)

**Role**: Injects the current UTC timestamp into the request payload and appends it to the response.

This gives the model temporal awareness — knowing *when* each message was sent helps maintain coherence across long conversations and makes agentic flows traceable. There is emerging evidence that timestamp markers also improve the model's ability to order information chronologically, reducing confusion about which decision came first.

```yaml
filter_chain:
  order: [timestamp]
  filters:
    timestamp:
      enabled: true
```

The timestamp appears as a system-level field in the payload. It does not modify the user's message content.

### Summarization (p15)

**Role**: Automatically summarizes older messages when the conversation exceeds the configured context window.

This filter operates on the **complete non-streaming response**. When the total token count (prompt + completion) exceeds `ctx_len`, it selects a representative portion of the conversation, sends it to the `summary_model` for condensation, and injects the summary as a system message in the next request.

The summarization engine supports three strategies:
- **classic** — rewrite the entire conversation into a concise summary
- **structured** — preserve key facts, decisions, and action items
- **incremental** — extend an existing summary with new content

```yaml
filter_chain:
  order: [summarization]
  filters:
    summarization:
      enabled: true
      strategy: classic          # classic, structured, or incremental
      summary_model: qwen3.5-4b  # optional: separate model for summaries
```

Configuration per-route:

```yaml
routes:
  chat/long:
    upstream_url: "http://llm:8080"
    ctx_len: 32768
    summary_enabled: true
    summary_model: "qwen3.5-4b"
```

### MultimodalValidator (p30)

**Role**: Validates and repairs multimodal (vision) payloads to prevent tokenizer errors.

When a message contains image markers in text (e.g. `<__media__>`, `<image>`) but the number of markers doesn't match the actual `image_url` parts, the upstream tokenizer raises an error: *"number of bitmaps (N) does not match number of markers (M)"*. This filter detects and fixes such mismatches.

Key behaviors:
- **Orphaned marker stripping**: Removes image markers from text when there are more markers than `image_url` items.
- **`<__media__>` unconditional strip**: The llama.cpp `mtmd` marker `<__media__>` is always stripped from text, since the server auto-inserts the correct count based on `image_url` items.
- **Max images enforcement**: Optionally limits the number of `image_url` items per request; excess images are replaced with a text placeholder.
- **Strip-all kill-switch**: Replace all `image_url` items with placeholder text when upstream can't handle any images.

```yaml
filter_chain:
  order: [multimodal_validator]
  filters:
    multimodal_validator:
      enabled: true
      strip_orphaned_markers: true   # Remove markers when count mismatches
      marker_patterns:               # Regex patterns to detect image markers
        - "<__media__>"
        - "<image>"
      max_images: 0                  # 0 = no limit
      strip_all_images: false        # Kill-switch: replace all images with text
```

### ToolLoopStopper (p25)

**Role**: Detects when the model repeatedly calls the same tool with the same arguments.

```yaml
filter_chain:
  order: [tool_loop_stopper]
  filters:
    tool_loop_stopper:
      enabled: true
      max_repeats: 3
      tls_message: "You just called {name}(). Stop repeating."
      loop_patterns: ["consecutive", "fuzzy", "ab"]
```

Loop modes:
- **consecutive**: N identical calls in a row
- **fuzzy**: N calls in a lookback window (allows interleaved non-loop calls)
- **ab**: A→B→A→B alternating pattern

### ReasoningLoopStopper (p20)

**Role**: Detects repeated identical `reasoning_content` blocks across turns.

Some models repeat the same reasoning text verbatim. RLS detects this and triggers a retry. It also cross-checks tool calls: same reasoning + different tool call = not a loop.

### ToolRewrite (p20)

**Role**: Converts XML pseudo-tool-calls (`<tool_call><function=name><parameter=key>value</parameter></function></tool_call>`) into structured OpenAI tool_calls format.

Useful for backends that output tool calls as XML in the content field instead of structured JSON.

### Timestamp (p100)

**Role**: Injects current UTC timestamp into the request payload and appends it to the response.

## Streaming Behavior

All filters run on every streaming chunk via `process_stream_chunk`. The base `Filter` class provides a pass-through implementation that forwards each chunk unchanged. Filters that need custom behavior (buffering, retry, stop) override this method:

- **ModelNudge** — buffers chunks, detects lazy responses on `finish_reason`, retries upstream
- **ToolLoopStopper** — buffers chunks, detects repeated tool calls
- **ReasoningLoopStopper** — buffers chunks, detects repeated reasoning blocks
- **ToolRewrite** — rewrites XML tool calls in streaming chunks

Filters without custom overrides (SystemPrompt, Summarization, Timestamp, MultimodalValidator) use the default pass-through and only modify requests/responses via `process_request` / `process_response`.
