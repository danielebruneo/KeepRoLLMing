# Keeprollming Orchestrator - Knowledge Base

## Project Overview

Keeprollming is a FastAPI-based proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds rolling-summary support to avoid context overflow. It handles long conversations by implementing a rolling summary mechanism that periodically summarizes conversation history while preserving the most recent user messages.

## Core Architecture

### Main Components
1. **FastAPI Application** (`keeprollming/app.py`) - Handles incoming requests, processes them through orchestrator logic, and sends responses back to client
2. **Configuration Management** (`keeprollming/config.py`) - Dataclass-based routing system with hierarchical profile inheritance using `extends` field
3. **Routing System** (`keeprollming/routing.py`) - Route matching, pattern parsing, and inherited route resolution via `resolve_inherited_route()`
4. **Orchestrator Logic** (`keeprollming/rolling_summary.py`) - Handles token counting, message splitting, and summarization as needed
5. **Upstream Client** (`keeprollming/upstream.py`) - Manages communication with OpenAI-compatible backend using `httpx.AsyncClient`
6. **Rolling Summary Module** (`keeprollming/rolling_summary.py`) - Implements core logic for handling context overflow and summary generation
7. **Summary Cache** (`keeprollming/summary_cache.py`) - Provides caching mechanisms to reuse previously generated summaries for efficiency

### Route Composition System
- Routes can extend other routes using the `extends` field in YAML config
- Child routes inherit all settings from parent unless explicitly overridden
- Supports multi-level inheritance chains (grandparent → parent → child)
- Circular inheritance detection prevents infinite loops
- Default values defined in `Route` dataclass, `_UNSET` sentinel distinguishes "not set" vs "explicitly set to default"

### Profile Categories
1. **Base Profiles** - Common settings for groups of routes (e.g., `quick-base`, `main-base`, `deep-base`)
2. **Chat Routes** - `chat/quick`, `chat/main`, `chat/deep` with varying context/token limits
3. **System Routes** - `sys/memory`, `sys/summary` for system-level tasks
4. **Code Routes** - `code/junior`, `code/senior`, `code/architect` for coding assistance at different levels

## Key Features

- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- Support for multiple profiles (`local/quick`, `local/main`, `local/deep`) with different model configurations
- Rolling-summary support to manage context overflow
- Passthrough mode for direct routing without summarization
- Streaming proxy (SSE) support
- Token accounting and context management

## Configuration

### Route Configuration Format
Routes can be defined in YAML using either list or dict format. Dict format supports inheritance:

```yaml
routes:
  # Base profile with common settings
  quick-base:
    pattern: "local/quick|quick"
    upstream_url: "http://arkai.local:1234/v1"
    main_model: "qwen3.5-35b-a3b@q3_k_s"
    ctx_len: 128000
    max_tokens: 8192

  # Child route that extends base and overrides specific fields
  chat/quick:
    pattern: "chat/quick|c/q"
    extends: quick-base
    main_model: "qwen2.5-3b-instruct"
    max_tokens: 4096
```

### Profile Categories (Current Setup)

**Base Profiles:**
- `quick-base`: ArkAI port 1234, ctx=128K, max_tokens=8K
- `main-base`: ArkAI port 1234, ctx=128K, max_tokens=16K  
- `deep-base`: ArkAI port 1234, ctx=128K, max_tokens=32K

**Chat Routes (extend base profiles):**
- `chat/quick` → extends `quick-base`, uses qwen2.5-3b-instruct
- `chat/main` → extends `main-base`, uses qwen3.5-35b-a3b
- `chat/deep` → extends `deep-base`, uses qwen3.5-35b-a3b

**System Routes (standalone):**
- `sys/memory`: ctx=64K, for memory and context retention
- `sys/summary`: ctx=64K, for summarization tasks

**Code Routes (extend base profiles):**
- `code/junior` → extends `quick-base`, uses qwen2.5-7b-instruct
- `code/senior` → extends `main-base`, uses qwen3.5-35b-a3b
- `code/architect` → extends `deep-base`, uses qwen3.5-35b-a3b

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

## Performance Monitoring

### Dashboard (`perf_dashboard.py`)
Real-time terminal dashboard for monitoring model performance metrics.

**Usage:**
```bash
python perf_dashboard.py                    # Auto-detects PERFORMANCE_LOGS_DIR
python perf_dashboard.py /path/to/summary   # Specify custom path
```

**Key Bindings:**
- `Ctrl+C` or `q`: Quit dashboard
- `c`: Clear logs (removes `__perf_logs` directory)
- `s`: Save timestamped backup of `summary.yaml` to `__perf_logs/backups/`

**Displayed Metrics:**
- Model name
- Total requests
- TPS (tokens per second)
- Completion TPS
- Prompt TPS
- TTFT (Time to First Token in ms)
- Completion tokens
- Prompt tokens

**Data Source:**
- Reads from `summary.yaml` in the performance logs directory
- Default path: `./__performance_logs/summary.yaml`
- Can be customized via `PERFORMANCE_LOGS_DIR` environment variable

### Benchmarking (`benchmark_routes.py`)
Route benchmarking tool for measuring performance across different routes.

**Usage:**
```bash
python benchmark_routes.py --num-prompts 5 --filter "chat/main"
```

**Arguments:**
- `--num-prompts`: Number of prompt iterations per route (default: varies)
- `--filter`: Filter routes by pattern (e.g., "chat/main", "code/*")
- Output groups results by backend_model instead of route

## Usage Examples

### Basic Route Matching
```bash
# Quick chat (uses qwen2.5-3b-instruct, inherits ctx from quick-base)
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"chat/quick","messages":[{"role":"user","content":"ciao"}]}'

# Senior code assistance (extends main-base, uses qwen3.5-35b-a3b)
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"code/senior","messages":[{"role":"user","content":"review this code"}]}'

# System memory (standalone route, ctx=64K)
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"sys/memory","messages":[{"role":"user","content":"save this context"}]}'
```

### Pattern Aliases
All routes support short aliases:
- `chat/quick` or `c/q`
- `chat/main` or `c/m`
- `code/senior` or `c/sn`
- etc.

### Passthrough Mode
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
- [_project/TODOS.md](_project/TODOS.md): Project enhancement wishlist

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


## CATALYST Cognitive Workflow

CATALYST distinguishes between runtime state and durable knowledge:
- Runtime state lives under `_agent/state/` and should be read first
- Durable knowledge lives under `_agent/knowledge/` and should be read as needed

The preferred cognitive sequence is:
1. THINK
2. PLAN (optional)
3. WORK
4. FEEDBACK
5. LEARN
6. ADAPT (small, safe, local only)
7. CLOSE-TASK

### Intent of core cognitive skills
- THINK: clarify the current objective, check scope, and select the next skill without modifying files
- PLAN: produce a bounded plan before execution when a task is complex or underspecified
- LEARN: consolidate lessons and decide whether they should remain as proposals, become TODOs, or justify ADAPT
- ADAPT: apply a minimal workflow or skill refinement; never broad architectural changes


## CATALYST cognitive routing
- [THINK](../../_skills/THINK/SKILL-THINK.md) is the cognitive router and should be preferred before premature skill use when the next step is unclear.
- [FEEDBACK](../../_skills/FEEDBACK/SKILL-FEEDBACK.md) analyzes recent friction and should recommend an explicit outcome.
- [LEARN](../../_skills/LEARN/SKILL-LEARN.md) handles broader consolidation and may recommend THINK or ADAPT.
- [ADAPT](../../_skills/ADAPT/SKILL-ADAPT.md) is allowed to change CATALYST repository artifacts when the current scope is `CATALYST` or `META`, as long as the change is small, local, and low-risk.