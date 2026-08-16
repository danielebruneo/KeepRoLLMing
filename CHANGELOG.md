# Changelog

All notable changes to this project will be documented in this file.

## Unreleased

### Added
- Route-level `reasoning_effort` overrides forward LibreChat-compatible
  reasoning controls to Qwen and other OpenAI-compatible upstreams.
- Each completed request emits an `execution.chat.performance_metrics` PLAIN
  event with the same derived values persisted for the performance dashboard.

### Changed
- Public onboarding now uses a verified Python/venv setup path, canonical
  `filters:` configuration examples, and a fake-backend quick-start E2E test.
- `config.example.full.yaml` is the single complete public configuration
  reference; historical streaming design documents are dev-only.
- Performance records now retain logical prompt tokens separately from
  `cached_prompt_tokens` and `uncached_prompt_tokens`.

### Fixes
- **V2 NudgeContinuationFinalizer recovery** now sends `request_payload_patch`
  using the `messages` key. The previous `nudge_message` key was silently
  ignored by `_apply_request_payload_patch()`, causing retries to reuse the
  original payload and eventually fall back. Nudge continuation now appends
  the lazy assistant prefix and continuation prompt to the upstream request.
- `prompt_tps` and `total_tps` no longer count KV-cached prompt tokens as
  prefill work, avoiding inflated throughput values for long cached contexts.

## v0.9.1 — 2026-06-27

Bug fixes and remote API support.

Additions:
- Route-level `api_key` field for remote OpenAI-compatible providers (DeepSeek, OpenRouter, etc.)
- `api_key` automatically flows to filter retry calls
- API authentication via `upstream_headers` or `api_key`
- Reasoning content pass-through (`delta.reasoning_content`) during nudge buffering
- Flexible timestamp footer regex (supports any template format)
- Orphaned buffer recovery on stream exhaustion

Fixes:
- SSE keepalive no longer kills the generator chain (`asyncio.wait_for` → `asyncio.wait FIRST_COMPLETED`)
- Nudge filter no longer flushes on intermediate tool_call/reasoning chunks
- TimestampFilter replaces stale timestamps instead of skipping (also added flexible regex)
- Phase 4 no longer duplicates content or yields stale timestamps
- Tool_call finish_reason yielded in a separate chunk (LibreChat compatibility)
- Tool_call fragments merged by index with delta arguments concatenation
- Nudge buffer saved before retry (original lazy content preserved in Phase 4)
- LibreChat tool_call execution fixed (raw fragments + finish_reason pass-through)
- `_extract_assistant_text` restored (was accidentally deleted by sed)
- `pipeline_phase4_error` added to log whitelist
- Route `ctx_len` / `max_tokens` inheritance through extends chain
- Direct streaming fallback now includes model response in error finish

## v0.9.0 — 2026-06-25

Initial stable release.

Key features:
- OpenAI-compatible chat completions endpoint (streaming + non-streaming)
- Composable filter pipeline with pluggable filters
- Rolling summarization for context window management
- Route system with pattern matching and extends inheritance
- Structured BASIC_PLAIN logging

Filter pipeline:
- SystemPrompt: inject/override system prompt
- ModelNudge: lazy response detection and auto-retry
- ToolRewrite: XML pseudo-tool-call → structured tool_calls
- ToolLoopStopper: detect repeated tool call loops
- ReasoningLoopStopper: detect repeated reasoning patterns
- Timestamp: inject UTC timestamp
- MultimodalValidator: validate multimodal requests

See README.md for installation and usage.
