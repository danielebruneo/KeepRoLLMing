# Keeprollming Orchestrator - Knowledge Base

## Project Overview

Keeprollming is a FastAPI-based proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds **rolling-summary** support to avoid context overflow. It handles long conversations by implementing a rolling summary mechanism that periodically summarizes conversation history while preserving the most recent user messages.

## Core Architecture

### Main Components
1. **FastAPI Application** (`keeprollming/app.py`) - Handles incoming requests, processes them through orchestrator logic, and sends responses back to client
2. **Configuration Management** (`keeprollming/config.py`) - Dataclass-based routing system with hierarchical profile inheritance using `extends` field
3. **Routing System** (`keeprollming/routing.py`) - Route matching, pattern parsing, and inherited route resolution via `resolve_inherited_route()`
4. **Orchestrator Logic** (`keeprollming/rolling_summary.py`) - Handles token counting, message splitting, and summarization as needed
5. **Upstream Client** (`keeprollming/upstream.py`) - Manages communication with OpenAI-compatible backend using `httpx.AsyncClient`
6. **Summary Cache** (`keeprollming/summary_cache.py`) - Provides caching mechanisms to reuse previously generated summaries for efficiency
7. **Token Counter** (`keeprollming/token_counter.py`) - Token estimation with fallback to character-based counting
8. **Logger** (`keeprollming/logger.py`) - Logging with multiple modes (DEBUG, MEDIUM, BASIC, BASIC_PLAIN)
9. **Performance** (`keeprollming/performance.py`) - Performance tracking utilities
10. **Metrics** (`keeprollming/metrics.py`) - Metrics collection and recording
11. **Validator** (`keeprollming/validator.py`) - Configuration validation tool

### Route Composition System
- Routes can extend other routes using the `extends` field in YAML config
- Child routes inherit all settings from parent unless explicitly overridden
- Supports multi-level inheritance chains (grandparent → parent → child)
- Circular inheritance detection prevents infinite loops
- Default values defined in `Route` dataclass, `_UNSET` sentinel distinguishes "not set" vs "explicitly set to default"
- Full route hierarchy path tracking in `_route_hierarchy` field

### Profile Categories
1. **Base Profiles** - Common settings for groups of routes (e.g., `quick-base`, `main-base`, `deep-base`)
2. **Chat Routes** - `chat/quick`, `chat/main`, `chat/deep` with varying context/token limits
3. **System Routes** - `sys/memory`, `sys/summary` for system-level tasks
4. **Code Routes** - `code/junior`, `code/senior`, `code/architect` for coding assistance at different levels
5. **Passthrough Routes** - Direct routing without summarization using pattern transformations

## Key Features

- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- Support for multiple profiles (`local/quick`, `local/main`, `local/deep`) with different model configurations
- Rolling-summary support to manage context overflow
- Passthrough mode for direct routing without summarization
- Streaming proxy (SSE) support with full response reconstruction
- Token accounting and context management
- Route inheritance with multi-level chains
- Circuit breaker support for fallback chains
- Performance monitoring and benchmarking
- Configuration validation tool
- Summary cache with fingerprint-based lookup and incremental consolidation
- Custom summary prompt templates (classic, curated, structured, incremental)
- Regex capture group support for backend model name transformation

## Configuration

### Route Configuration Format
Routes can be defined in YAML using either list or dict format. Dict format supports inheritance:

```yaml
routes:
  # Base profile with common settings
  quick-base:
    pattern: "local/quick|quick"
    upstream_url: "http://arkai.local:1234/v1"
    model: "qwen3.5-35b-a3b@q3_k_s"
    ctx_len: 128000
    max_tokens: 8192

  # Child route that extends base and overrides specific fields
  chat/quick:
    pattern: "chat/quick|c/q"
    extends: quick-base
    model: "qwen2.5-3b-instruct"
    max_tokens: 4096  # Override parent's max_tokens
```

### Global Defaults
Global settings can be defined at the root level and applied to all routes unless overridden:

```yaml
defaults:
  ctx_len: 8192
  max_tokens: 4096  # Optional default for all routes

# If default_max_completion_tokens is not set or is None, max_tokens is NOT sent upstream
default_max_completion_tokens: 900  # Optional fallback when client doesn't specify
```

**max_tokens behavior:**
- If `max_tokens` is configured at route level → use that value
- If `max_tokens` is configured at root level (defaults.max_tokens) → use as default for all routes
- If `default_max_completion_tokens` is set in config → use when client doesn't specify max_tokens
- If no max_tokens configuration exists → **do NOT send max_tokens upstream** (upstream decides)

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
- `LOG_PAYLOAD_MAX_CHARS` - Maximum characters for logging large payloads
- `LOG_STREAM_PROGRESS_INTERVAL_MS` - Interval for logging stream progress
- `SUMMARY_FORCE_CONSOLIDATE` - Force summary consolidation
- `SUMMARY_CONSOLIDATE_WHEN_NEEDED` - Conditional summary consolidation
- `ENABLE_OPENAI_STREAM_COMPAT` - Enable OpenAI streaming compatibility mode

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

### Request Flow
1. Client sends request to `/v1/chat/completions` endpoint
2. Application parses request payload and extracts model, messages, stream flag
3. Model resolution: determines profile or passthrough mode based on client model
4. Message splitting: separates system messages from regular messages
5. Summarization decision: evaluates whether context exceeds threshold using token counting
6. If summarization needed:
   - Cache lookup for existing summary (if enabled)
   - If cache miss, generate new summary from middle portion of conversation
   - If cache hit, potentially reuse cached summary with incremental updates
7. Request repacking: combines head messages, summary, and tail messages into new prompt
8. Context adjustment: calculates max tokens for upstream request based on estimated prompt length
9. Forward to upstream backend via HTTP client
10. Receive response from upstream backend
11. If streaming, reconstruct the full assistant reply from SSE events
12. Return final response to client

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
9. Configuration validation and inheritance chains

### Test Files
- `test_config.py` - Configuration loading and profile resolution
- `test_routing.py` - Route matching and pattern parsing
- `test_orchestrator.py` - Core orchestration logic
- `test_summary_overflow_regression.py` - Summary overflow handling
- `test_validator.py` - Configuration validation
- `test_healthcheck.py` - E2E health checks

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
- Route hierarchy (full inheritance chain)

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

**New Metrics:**
- `prompt_tps` - Tokens per second during prompt processing (before first token)
- `completion_tps` - Tokens per second during generation (after first token)
- `tps` - Overall tokens per second for entire request (prompt + completion combined)

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

### Configuration Validation
```bash
# Validate configuration structure
python validate_config.py --config config.yaml validate

# Run E2E health checks on all routes
python validate_config.py --config config.yaml healthcheck

# Run full validation (structure + health)
python validate_config.py --config config.yaml full-check
```

## Error Handling

The orchestrator implements comprehensive error handling with automatic fallback chains:

**Error Categories:**
- `connection_failed`: httpx.ConnectError - upstream server unreachable
- `connection_timeout`: httpx.ConnectTimeout - connection timed out
- `timeout`: httpx.TimeoutException - request timeout
- `http_status_error`: HTTP errors from upstream (4xx, 5xx)
- `stream_reconstruction_error`: Errors during streaming response processing

**Error Log Format (keeprollming.log):**
```
2026-03-21 14:32:15 | ERROR    | keeprollming | connection_error | req_id=abc123 | model=gpt-4 | upstream_url=https://api.example.com | err=All connection attempts failed
```

**Key Fields:**
- `req_id`: Unique request identifier (6-char hex)
- `model`: Requested client model
- `endpoint`: Route endpoint being used
- `upstream_url`: Target upstream server URL
- `status`: HTTP status code (if applicable)
- `error_type`: Categorized error type
- `err`: Error message (truncated to 500 chars)

**Fallback Chain Behavior:**
- On connection errors, the orchestrator automatically attempts fallback models
- Each failed attempt is logged with `fallback_error` event
- If all fallbacks exhausted, returns error to client with details

## Configuration Validation

The project includes a comprehensive configuration validation tool to check:
- **Route inheritance chains** - Validates that routes properly extend parent routes
- **Circular references** - Detects circular inheritance (e.g., A extends B extends A)
- **Required fields** - Ensures non-private routes have all necessary settings
- **E2E health checks** - Tests actual backend connectivity

**Exit Codes:**
- `0` - Validation passed, all routes healthy
- `1` - Validation failed or some routes unhealthy

## Prompt Templates

Summary prompts are loaded from external template files in the `_prompts/` directory.

### Available Templates
- `classic.summary_prompt.txt` - Classic summarization format
- `curated.summary_prompt.txt` - Curated context compaction (default)
- `structured.summary_prompt.txt` - Structured bullet-point format
- `incremental.txt` - Incremental summary updates

### Template Variables
- `{{TRANSCRIPT}}` - The conversation transcript to summarize
- `{{LANG_HINT}}` - Language hint for output (default: "italiano")

## Cross-referencing

- [_docs/architecture/OVERVIEW.md](../_docs/architecture/OVERVIEW.md): Architecture overview
- [_docs/architecture/INVARIANTS.md](../_docs/architecture/INVARIANTS.md): System invariants
- [_docs/decisions/DECISIONS.md](../_docs/decisions/DECISIONS.md): Design decisions
- [_docs/development/STYLE.md](../_docs/development/STYLE.md): Coding conventions
- [_docs/development/WORKFLOW.md](../_docs/development/WORKFLOW.md): Development workflow
- [_docs/API_DOCUMENTATION.md](../_docs/API_DOCUMENTATION.md): API reference
- [_docs/CONFIGURATION.md](../_docs/CONFIGURATION.md): Configuration guide
- [_docs/PERFORMANCE.md](../_docs/PERFORMANCE.md): Performance optimization
- [_docs/TESTING.md](../_docs/TESTING.md): Testing guidelines
- [_docs/TROUBLESHOOTING.md](../_docs/TROUBLESHOOTING.md): Common issues
- [_project/TODOS.md](_project/TODOS.md): Project enhancement wishlist

## Repository Structure

```
.
├── keeprollming/           # Core application modules
│   ├── app.py             # FastAPI application
│   ├── config.py          # Configuration management
│   ├── routing.py         # Route matching and resolution
│   ├── rolling_summary.py # Core summarization logic
│   ├── summary_cache.py   # Summary caching
│   ├── upstream.py        # Upstream client
│   ├── token_counter.py   # Token counting
│   ├── logger.py          # Logging
│   ├── performance.py     # Performance tracking
│   ├── metrics.py         # Metrics collection
│   ├── validator.py       # Configuration validation
│   └── healthcheck.py     # Health check endpoints
├── tests/                  # Unit and integration tests
├── _prompts/              # Summary prompt templates
├── _docs/                 # Documentation
│   ├── architecture/      # Architecture docs
│   ├── decisions/         # Decision records
│   ├── design/           # Design docs
│   └── development/      # Development guides
├── _project/              # Project metadata
│   ├── _docs/            # Project documentation
│   ├── _project/         # Project-specific docs
│   └── _skills/          # Project skills
├── _agent/                # Agent state and knowledge
│   ├── state/            # Runtime state
│   ├── knowledge/        # Persistent knowledge
│   └── learning_reports/ # Learning sessions
├── benchmarks/            # Benchmark results
├── __performance_logs/    # Performance data
├── __summary_cache/       # Summary cache storage
├── benchmark_routes.py    # Benchmark tool
├── perf_dashboard.py      # Performance dashboard
├── validate_config.py     # Config validation tool
├── config.example.yaml    # Example configuration
├── requirements.txt       # Production dependencies
└── requirements-dev.txt   # Development dependencies
```

## Dependencies

**Production:**
- `fastapi>=0.112` - Web framework
- `uvicorn[standard]>=0.30` - ASGI server
- `httpx>=0.27` - Async HTTP client
- `pydantic>=2.8` - Data validation
- `rich>=13.7` - Terminal UI
- `python-multipart>=0.0.9` - Multipart form support

**Development:**
- `pytest>=8.0` - Testing framework
- `pytest-asyncio>=0.23` - Async test support
- `pytest-xdist>=3.0` - Parallel test execution

## CATALYST Bootstrap Model

This repository uses a layered bootstrap for agent-assisted development:
- [QWEN.md](../QWEN.md): Qwen-specific loader
- [AGENTS.md](../AGENTS.md): canonical workflow and rules
- [README.md](../README.md): human-facing overview with a short agent-assistance section

Operational state lives in [_agent/](_agent/) and specialized procedures live in [_skills/](_skills/).

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

## Related Skills

- [ADD-FEATURE](../_skills/ADD-FEATURE/SKILL-ADD-FEATURE.md)
- [BUILD-REPO-MAP](../_skills/BUILD-REPO-MAP/SKILL-BUILD-REPO-MAP.md)