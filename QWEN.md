# Keeprollming Orchestrator

This is a FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds **rolling-summary** support to avoid context overflow.

## Agent Working Patterns

When working with skills in CATALYST projects, be aware that some files like SKILL-FEEDBACK.md are symlinks to SKILL.md. Only edit the actual content file (SKILL.md) as both links point to the same content. Always run 'ls -la' first to check for symlink relationships before making any modifications.

This pattern was encountered during an improvement of the FEEDBACK skill where I initially confused the files and needed to understand that they are functionally identical.

## Project Overview

The Keeprollming Orchestrator is designed to handle long conversations that would otherwise exceed the context window limits of language models. It implements a rolling summary mechanism that periodically summarizes conversation history while preserving the most recent user messages.

### Key Features
- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- Support for multiple profiles (`local/quick`, `local/main`, `local/deep`) with different model configurations
- Rolling-summary support to manage context overflow
- Passthrough mode for direct routing without summarization
- Streaming proxy (SSE) support
- Token accounting and context management

### Architecture
1. **FastAPI Application** (`keeprollming/app.py`) - Handles incoming requests, processes them through the orchestrator logic, and sends responses back to the client.
2. **Configuration Management** (`keeprollming/config.py`) - Uses a dataclass-based system for profiles with different main and summary models.
3. **Orchestrator Logic** - Handles token counting, message splitting, and summarization as needed.
4. **Upstream Client** (`keeprollming/upstream.py`) - Manages communication with the OpenAI-compatible backend using `httpx.AsyncClient`.
5. **Rolling Summary Module** (`keeprollming/rolling_summary.py`) - Implements the core logic for handling context overflow and summary generation.
6. **Summary Cache** (`keeprollming/summary_cache.py`) - Provides caching mechanisms to reuse previously generated summaries for efficiency.

## Configuration

Configuration is managed via:
- `config.yaml` file with profiles and model aliases
- Environment variables that override default values
- Available profiles: 
  - `local/quick` (qwen2.5-3b-instruct main, qwen2.5-1.5b-instruct summary)
  - `local/main` (qwen2.5-v1-7b-instruct main, qwen2.5-3b-instruct summary)
  - `local/deep` (qwen/qwen3.5-35b-a3b main, qwen2.5-7b-instruct summary)

## Running the Application

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export UPSTREAM_BASE_URL="http://127.0.0.1:1234"   # LM Studio base (no /v1)
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000
```

Then call:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'
```

## Testing

Install dev requirements:
```bash
pip install -r requirements-dev.txt
```

Run tests using:
```bash
pytest
```

Or use dedicated test scripts for better environment management:
```bash
./run-tests.sh          # Run all tests in serial mode
./run-single-test.sh    # Run a single test
./run-parallel-tests.sh # Run tests in parallel mode
```

## Key Environment Variables

- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `SUMMARY_MODE` (default `cache_append`)
- `SUMMARY_CACHE_ENABLED` (default `true`)
- `SUMMARY_CACHE_DIR` (default `./__summary_cache`)

## Development Conventions

- Uses pytest for testing with mock upstream calls
- Tests are unit/integration-ish but don't require a live LM Studio instance
- Uses FastAPI framework with async/await patterns
- Follows Python coding standards with proper typing annotations
- Has comprehensive logging capabilities for debugging

## Usage Examples

### Basic Usage:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'
```

### Passthrough Mode:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"pass/gpt-4","messages":[{"role":"user","content":"ciao"}]}'
```

### Streaming:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","stream":true,"messages":[{"role":"user","content":"ciao"}]}'
```

## Qwen Added Memories
- When working with skills in CATALYST projects, be aware that some files like SKILL-FEEDBACK.md are symlinks to SKILL.md. Only edit the actual content file (SKILL.md) as both links point to the same content. Always run 'ls -la' first to check for symlink relationships before making any modifications.
