# Changelog

All notable changes to this project will be documented in this file.

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
