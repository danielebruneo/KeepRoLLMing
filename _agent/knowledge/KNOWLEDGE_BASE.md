# Keeprollming Orchestrator - Knowledge Base

## Project Overview

Keeprollming is a FastAPI-based proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds rolling-summary support to avoid context overflow. It handles long conversations by implementing a rolling summary mechanism that periodically summarizes conversation history while preserving the most recent user messages.

## Core Architecture

### Main Components
1. **FastAPI Application** (`keeprollming/app.py`) - Handles incoming requests, processes them through orchestrator logic, and sends responses back to client
2. **Configuration Management** (`keeprollming/config.py`) - Uses dataclass-based system for profiles with different main and summary models
3. **Orchestrator Logic** (`keeprollming/rolling_summary.py`) - Handles token counting, message splitting, and summarization as needed
4. **Upstream Client** (`keeprollming/upstream.py`) - Manages communication with OpenAI-compatible backend using `httpx.AsyncClient`
5. **Rolling Summary Module** (`keeprollming/rolling_summary.py`) - Implements core logic for handling context overflow and summary generation
6. **Summary Cache** (`keeprollming/summary_cache.py`) - Provides caching mechanisms to reuse previously generated summaries for efficiency

## Key Features

- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- Support for multiple profiles (`local/quick`, `local/main`, `local/deep`) with different model configurations
- Rolling-summary support to manage context overflow
- Passthrough mode for direct routing without summarization
- Streaming proxy (SSE) support
- Token accounting and context management

## Configuration

### Profiles
- `local/quick` (qwen2.5-3b-instruct main, qwen2.5-1.5b-instruct summary)
- `local/main` (qwen2.5-v1-7b-instruct main, qwen2.5-3b-instruct summary)
- `local/deep` (qwen/qwen3.5-35b-a3b main, qwen2.5-7b-instruct summary)

### Environment Variables
- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `DEFAULT_CTX_LEN` - Default context length when no model info is available
- `SUMMARY_MAX_TOKENS` - Maximum tokens for summary generation
- `SAFETY_MARGIN_TOK` - Safety margin to avoid exact context limits
- `SUMMARY_MODE` (default `cache_append`) - Summary strategy choice
- `SUMMARY_CACHE_ENABLED` (default `true`) - Toggle cache usage
- `SUMMARY_CACHE_DIR` (default `./__summary_cache`) - Cache storage directory path
- `SUMMARY_CACHE_FINGERPRINT_MSGS` - Number of messages to include in fingerprint calculation
- `LOG_MODE` (DEBUG, MEDIUM, BASIC, BASIC_PLAIN) - Logging verbosity level

## How It Works

### Summary Decision Process
1. Token counting for incoming messages
2. Determine if summarization is needed based on context length and threshold
3. Choose optimal head/tail sizes to preserve important context while allowing summarization of middle portion
4. Apply rolling summary when needed

### Rolling Summary Strategy
- When a conversation exceeds the context window, the system:
  - Preserves the first few user messages (head)
  - Summarizes the middle portion of the conversation
  - Preserves the last few messages (tail)
  - Reconstructs the final prompt with the summary inserted

### Cache Management
- Uses fingerprint-based caching to reuse previously generated summaries
- Supports incremental reuse for efficient updates
- Maintains cache entries with range hashes for validation
- Implements both full and partial cache entry matching strategies

## Testing

### Test Setup
- Uses `pytest` framework with mock upstream calls
- Tests are unit/integration-ish but do not require live LM Studio instance
- Mocking is done in test fixtures to avoid real backend connections

### Key Test Areas
1. Passthrough mode functionality (no summarization)
2. Streaming proxy behavior
3. Rolling summary trigger and repacking logic
4. Summary cache hit/miss scenarios
5. Incremental summary consolidation
6. Context overflow handling with chunking
7. Performance metrics recording
8. Logging behavior under various modes

## Code Structure

### File Organization
- `keeprollming/app.py` - Main FastAPI application and request handling
- `keeprollming/config.py` - Configuration management with profiles
- `keeprollming/rolling_summary.py` - Core summarization logic and decision making
- `keeprollming/summary_cache.py` - Cache implementation and retrieval
- `keeprollming/upstream.py` - Upstream client communication
- `keeprollming/token_counter.py` - Token counting utilities
- `keeprollming/logger.py` - Logging functionality
- `keeprollming/performance.py` - Performance tracking utilities

## Usage Examples

Basic usage:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'
```

Passthrough mode:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"pass/gpt-4","messages":[{"role":"user","content":"ciao"}]}'
```

Streaming:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","stream":true,"messages":[{"role":"user","content":"ciao"}]}'
```

## Key Implementation Details

### Summary Strategy Options
- `cache_append` (default): Reuse existing summaries when possible, with incremental consolidation
- Classic approach: Always summarize middle portion from scratch

### Context Handling
- Uses safety margin tokens to avoid hitting exact context limits
- Dynamically calculates maximum output tokens based on available context
- Handles context overflow errors by chunking and retrying
- Implements fallback mechanism for upstream model info retrieval

### Logging Configuration
- Supports multiple logging modes: DEBUG, MEDIUM, BASIC, BASIC_PLAIN
- Provides detailed logging for debugging purposes
- Includes streaming response handling capabilities

## Cross-referencing

- [_docs/architecture/OVERVIEW.md](../_docs/architecture/OVERVIEW.md): Architecture overview
- [_docs/decisions/DECISIONS.md](../_docs/decisions/DECISIONS.md): Design decisions
- [_docs/development/STYLE.md](../_docs/development/STYLE.md): Coding conventions
- [_docs/development/WORKFLOW.md](../_docs/development/WORKFLOW.md): Development workflow

## Related Skills

- [ADD-FEATURE](../_skills/ADD-FEATURE/SKILL-ADD-FEATURE.md)
- [BUILD-REPO-MAP](../_skills/BUILD-REPO-MAP/SKILL-BUILD-REPO-MAP.md)

## CATALYST bootstrap model

This repository uses a layered bootstrap for agent-assisted development:
- [QWEN.md](../QWEN.md): Qwen-specific loader
- [AGENTS.md](../AGENTS.md): canonical workflow and rules
- [README.md](../README.md): human-facing overview with a short agent-assistance section

Operational state lives in [_agent/](../_agent/) and specialized procedures live in [_skills/](../_skills/).

Within skill directories, `SKILL.md` is canonical and any `SKILL-<NAME>.md` companion path should be treated as an alias/symlink path to the canonical content.
