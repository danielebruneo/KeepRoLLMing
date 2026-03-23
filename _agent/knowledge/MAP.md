# Keeprollming Orchestrator - Repository Map

## Project Purpose
A small FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds **rolling-summary** support to avoid context overflow.

## Boundary
This project is responsible for:
- Handling incoming requests through the `/v1/chat/completions` endpoint
- Managing conversation history to prevent context overflow by applying rolling summaries
- Providing passthrough mode for direct routing without summarization
- Supporting streaming proxy (SSE) responses
- Token accounting and context management

It is NOT responsible for:
- Model inference itself (delegates to upstream backend)
- User authentication or access control
- Storage of conversation history beyond runtime memory
- Database operations or persistence mechanisms

## Main Components
1. **FastAPI Application (`keeprollming/app.py`)**
   - Handles incoming requests, processes them through the orchestrator logic, and sends responses back to the client.
   - Implements request routing with passthrough mode support.

2. **Configuration Management (`keeprollming/config.py`)**
   - Uses a dataclass-based system for profiles with different main and summary models.
   - Supports multiple profiles: `local/quick`, `local/main`, `local/deep` with different model configurations.
   - Provides environment variable override capabilities.

3. **Orchestrator Logic (`keeprollming/rolling_summary.py`)**
   - Handles token counting, message splitting, and summarization as needed.
   - Implements the rolling summary mechanism to manage context overflow.
   - Supports various summary prompt types (classic, structured, curated).

4. **Upstream Client (`keeprollming/upstream.py`)**
   - Manages communication with the OpenAI-compatible backend using `httpx.AsyncClient`.
   - Fetches context length information from the upstream model.

5. **Summary Cache (`keeprollming/summary_cache.py`)**
   - Provides caching mechanisms to reuse previously generated summaries for efficiency.
   - Handles fingerprinting, loading and saving of cache entries.
   - Supports incremental summary consolidation.

6. **Token Counter (`keeprollming/token_counter.py`)**
   - Provides token estimation capabilities for messages and text content.
   - Fallback to character-based counting when tiktoken is not available.

7. **Logging (`keeprollming/logger.py`)**
   - Implements logging functionality with different modes (DEBUG, MEDIUM, BASIC).
   - Supports detailed logging for debugging and monitoring purposes.
   - Includes streaming response handling capabilities.

8. **Performance Tracking (`keeprollming/performance.py`)**
   - Implements performance measurement utilities for tracking request processing time and resource usage.
   - Records metrics like TPS (tokens per second) and TTFT (time to first token).

## Request / Data Flow
1. Client sends a request to `/v1/chat/completions` endpoint
2. Application parses the request payload and extracts model, messages, stream flag
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

## Failure / Fallback Philosophy
- When summarization fails, the system falls back to passthrough mode (direct routing)
- Context overflow errors are handled gracefully by retrying with reduced context
- Cache operations fail soft - if cache is unavailable or corrupted, continue without it
- Logging does not break core request serving - logging failures are ignored
- If upstream backend fails, the system attempts graceful degradation or error propagation

## Key Configuration Parameters
- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `DEFAULT_CTX_LEN` - Default context length when no model info is available
- `SUMMARY_MAX_TOKENS` - Maximum tokens for summary generation

## Links
- `_docs/architecture/OVERVIEW.md`
- `_docs/architecture/INVARIANTS.md`
- `_docs/decisions/DECISIONS.md`
- `_project/KNOWLEDGE_BASE.md` - Comprehensive project knowledge
- `_project/SKILLS-INDEX.md` - CATALYST skills catalog
- `CATALYST.md` - CATALYST workflow specification

## Repository Structure
- `keeprollming/` - Core application modules (app.py, config.py, rolling_summary.py, etc.)
- `tests/` - Unit and integration tests
- `scripts/` - Utility scripts for testing and setup
- `config.yaml` - Configuration file
- `requirements.txt` - Production dependencies
- `requirements-dev.txt` - Development dependencies
- `_docs/` - Documentation directory (CONFIGURATION.md, PERFORMANCE.md, TESTING.md, etc.)
- `_prompts/` - Summary prompt templates (classic, curated, structured, incremental)
- `_agent/` - Agent runtime state and knowledge
  - `_agent/state/` - Active task, handoff, scope
  - `_agent/knowledge/` - Project knowledge base, repo map (this file)
  - `_agent/learning_reports/` - Session-specific learning documentation
- `_project/` - Project-specific documentation and skills
  - `_project/_skills/` - Project skills (IMPROVE-SKILLS)
  - `_project/KNOWLEDGE_BASE.md` - Comprehensive knowledge base
  - `_project/TODOS.md` - Project enhancement wishlist
- `_agent/overlay/` - Agent overlay skills and configuration
- `.catalyst/` - CATALYST core skills and configuration
  - `.catalyst/_skills/` - 46 core CATALYST skills
  - `CATALYST.md` - CATALYST workflow specification
  - `AGENTS.md` - Agent workflow specification
- `.qwen/skills/` - Runtime skill registry (auto-generated symlinks)
- `perf_dashboard.py` - Real-time performance monitoring dashboard
- `benchmark_routes.py` - Route benchmarking tool

## Key Environment Variables
- `LOG_MODE` (DEBUG, MEDIUM, BASIC, BASIC_PLAIN) - Logging verbosity level
- `SUMMARY_MODE` (cache_append, classic) - Summary strategy choice
- `SUMMARY_CACHE_ENABLED` (true/false) - Toggle cache usage
- `SUMMARY_CACHE_DIR` - Cache storage directory path
- `LOG_PAYLOAD_MAX_CHARS` - Maximum characters for logging large payloads