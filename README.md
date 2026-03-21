# Keeprollming Orchestrator

This is a FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds **rolling-summary** support to avoid context overflow.

## Agent-assisted development

This repository uses **[CATALYST](CATALYST.md)** as its agent-assisted development workflow.

- [QWEN.md](QWEN.md) is the Qwen-specific bootstrap entrypoint.
- [AGENTS.md](AGENTS.md) is the canonical workflow specification for coding agents.
- [_agent/](_agent/) contains operational state such as the active task, handoff, knowledge base, and repo map.
- [_project/TODOS.md](_project/TODOS.md) contains the project's long-term enhancement wishlist.

If an agent runner passes through this README, it should continue into [QWEN.md](QWEN.md) and then [AGENTS.md](AGENTS.md) before making substantial changes.

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

## Configuration Validation

This project includes a comprehensive configuration validation tool to check:
- **Route inheritance chains** - Validates that routes properly extend parent routes
- **Circular references** - Detects circular inheritance (e.g., A extends B extends A)
- **Required fields** - Ensures non-private routes have all necessary settings
- **E2E health checks** - Tests actual backend connectivity

### Usage

```bash
# Validate configuration structure
python validate_config.py --config config.yaml validate

# Run E2E health checks on all routes
python validate_config.py --config config.yaml healthcheck

# Run full validation (structure + health)
python validate_config.py --config config.yaml full-check
```

### CLI Options

| Command | Description |
|---------|-------------|
| `validate` | Validate configuration structure and inheritance chains |
| `healthcheck` | Test backend connectivity for all routes |
| `full-check` | Run both validation and health checks |

### Exit Codes

- `0` - Validation passed, all routes healthy
- `1` - Validation failed or some routes unhealthy

## Performance Benchmarking

This project includes a benchmark tool to test route performance with predefined prompts:

```bash
# Run benchmarks on all routes
python benchmark_routes.py --config config.yaml --output benchmarks/

# Custom timeout and verbose output
python benchmark_routes.py --config config.yaml -t 120 -v
```

### Benchmark Features
- Tests multiple prompt types (story, technical explanation, code review)
- Measures latency, throughput (tokens/sec), and token counts
- **New metrics:**
  - `prompt_tps` - Tokens per second during prompt processing (before first token)
  - `completion_tps` - Tokens per second during generation (after first token)
  - `tps` - Overall tokens per second for entire request (prompt + completion combined)
- Saves individual route results as JSON files
- Generates aggregated summary statistics

### CLI Options

| Option | Description |
|--------|-------------|
| `--config` | Path to config file (default: `config.yaml`) |
| `--filter` | Filter routes by name pattern (e.g., `"chat/"`, `"local/"`) |
| `-n, --num-prompts` | Number of prompts to run per route (default: 3) |
| `-t, --timeout` | Request timeout in seconds (default: 60) |
| `-v, --verbose` | Enable verbose output |

## Real-Time Performance Dashboard

Monitor performance metrics in real-time with the terminal dashboard:

```bash
# Auto-detects PERFORMANCE_LOGS_DIR environment variable
python perf_dashboard.py

# Or specify custom summary path
python perf_dashboard.py /path/to/summary.yaml
```

### Dashboard Features
- **Per-model breakdown** - Metrics grouped by backend model
- **Live updates** - Automatically refreshes when `summary.yaml` changes
- **Key metrics displayed:**
  - Requests count per model
  - Overall TPS (total tokens/sec)
  - Completion TPS (generation throughput)
  - Prompt TPS (prompt processing throughput)
  - TTFT (Time to First Token in ms)
  - Average completion tokens

### Example Output
```
================================================================================
                       📊 PERFORMANCE MONITORING DASHBOARD                       
                           Real-time metrics by model                           
================================================================================

📅 Last updated: 2026-03-21 01:15:42
Model                          | Requests |      TPS |   Comp TPS |   Prompt TPS |  TTFT (ms) |  Comp Tokens
--------------------------------------------------------------------------------
qwen3.5-4B                     |       42 |    48.50 |      39.20 |       215.50 |     375.50 |          282
qwen3.5-35b-a3b@q3_k_s         |       22 |    60.20 |      44.60 |       162.70 |    1020.50 |          285
--------------------------------------------------------------------------------

📈 Total Requests: 64
📈 Avg TPS (all models): 52.52

💡 Press Ctrl+C to exit
================================================================================
```

## Testing

Run the test suite to verify validator functionality:

```bash
# Run all tests
pytest tests/

# Run only validator tests
pytest tests/test_validator.py -v

# Run only health check tests  
pytest tests/test_healthcheck.py -v
```

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

## Custom Summary Prompts

The orchestrator now supports loading custom summary prompt templates from the `_prompts` directory or from the config file directly. This allows users to define their own summarization prompts for more flexibility.

### How it works:

1. When providing `summary_prompt_type` field in a request, that name will be used to look up the template
2. If the value is found in the configuration's `custom_summary_prompts` section:
   - If it's a string that doesn't start with "|", it will be treated as a file path (relative to `_prompts`)
   - If it starts with "|", it will be treated as direct multi-line text content
3. Otherwise, templates are loaded from the `_prompts` directory

### Configuration Example:

In `config.yaml`, you can define custom prompt templates in the `custom_summary_prompts` section:
```yaml
# Custom summary prompt templates - can be either:
# - a file path (relative to _prompts directory)
# - direct text content for simple prompts
custom_summary_prompts:
  # Example of a custom prompt template that would be loaded from _prompts/custom.txt  
  my_custom_prompt: "./_prompts/example_custom.txt"
  
  # Example of direct text in config, which will be treated as the actual prompt content
  structured_explainer: |
    You are an expert explainer.
    Explain the following conversation transcript in a technical and concise way.

    === TRANSCRIPT START ===
    {{TRANSCRIPT}}
    === TRANSCRIPT END ===

    Your explanation should be clear, structured, and focused on key points only.
```

### Usage Examples:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"Explain quantum computing"}],
    "summary_prompt_type": "my_custom_prompt"
  }'
```

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"Explain quantum computing"}],
    "summary_prompt_type": "structured_explainer"
  }'
```

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{
    "model":"local/main",
    "messages":[{"role":"user","content":"Explain quantum computing"}],
    "summary_prompt_type": "custom",
    "summary_prompt": "You are an expert explainer. Please provide a concise explanation of the following conversation transcript."
  }'
```

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

### Streaming with Detailed Response Format:
```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","stream":true,"messages":[{"role":"user","content":"explain quantum computing in simple terms"}]}'
```

This streaming response will return multiple `data:` events, each containing a partial completion until the final chunk with `finish_reason` set to `"stop"`.

