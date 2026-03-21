# Configuration

## Environment Variables

- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1`)
- `SUMMARY_PROMPT_DIR` - Directory containing summary prompt templates (default: `./_prompts`)
- `SUMMARY_PROMPT_TYPE` - Prompt type to use (classic, curated, structured, incremental)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `DEFAULT_CTX_LEN` - Default context length when no model info is available
- `SUMMARY_MAX_TOKENS` - Maximum tokens for summary generation

## Prompt Template Configuration

Summary prompts are loaded from external template files in the `_prompts/` directory.

### Available Templates
- `classic.summary_prompt.txt` - Classic summarization format
- `curated.summary_prompt.txt` - Curated context compaction (default)
- `structured.summary_prompt.txt` - Structured bullet-point format
- `incremental.txt` - Incremental summary updates

### Custom Prompts in config.yaml

Custom prompts can be defined in the `custom_summary_prompts` section:

```yaml
custom_summary_prompts:
  classic: "./_prompts/classic.summary_prompt.txt"
  curated: "./_prompts/curated.summary_prompt.txt"
  structured: "./_prompts/structured.summary_prompt.txt"
  incremental: "./_prompts/incremental.txt"
```

Or use direct text prompts (no file path):

```yaml
custom_summary_prompts:
  custom-prompt: |
    You are a context compaction engine.
    {{TRANSCRIPT}}
    Output in Italian with {{LANG_HINT}} language hint.
```

### Template Variables
- `{{TRANSCRIPT}}` - The conversation transcript to summarize
- `{{LANG_HINT}}` - Language hint for output (default: "italiano")

## Configuration Files

The project now supports configuration through `config.yaml` or `config.json` files, which provide a more flexible and centralized approach to managing settings.

### File Format

The configuration file should contain a dictionary with the following keys:

```yaml
# config.yaml example
upstream_base_url: "http://127.0.0.1:1234/v1"
main_model: "qwen2.5-3b-instruct"
summary_model: "qwen2.5-1.5b-instruct"
quick_main_model: "qwen2.5-3b-instruct"
quick_summary_model: "qwen2.5-1.5b-instruct"
base_main_model: "qwen2.5-3b-instruct"
base_summary_model: "qwen2.5-1.5b-instruct"
deep_main_model: "qwen2.5-3b-instruct"
deep_summary_model: "qwen2.5-1.5b-instruct"
max_head: 1000
max_tail: 1000
default_ctx_len: 4096
summary_max_tokens: 512

profiles:
  quick:
    main_model: "qwen2.5-3b-instruct"
    summary_model: "qwen2.5-1.5b-instruct"
  main:
    main_model: "qwen2.5-3b-instruct"
    summary_model: "qwen2.5-1.5b-instruct"
  deep:
    main_model: "qwen2.5-3b-instruct"
    summary_model: "qwen2.5-1.5b-instruct"

model_aliases:
  local/quick: quick
  local/main: main
  local/deep: deep

passthrough_prefix: "pass/"
```

### Loading Priority

Configuration is loaded in the following order of priority:
1. Configuration file (`config.yaml` or `config.json`)
2. Environment variables (fallback for any missing values)
3. Default values (hardcoded fallbacks)

### Profiles

The configuration system supports arbitrary number of profiles beyond the default "quick", "main", and "deep". Users can define custom profiles in the `profiles` section:

```yaml
profiles:
  custom1:
    main_model: "gpt-4-turbo"
    summary_model: "gpt-3.5-turbo"
  custom2:
    main_model: "llama-2-70b"
    summary_model: "llama-2-13b"
```

### Model Aliases

Custom model aliases can be defined in the `model_aliases` section to enable flexible routing:

```yaml
model_aliases:
  local/custom1: custom1
  custom1: custom1
  local/custom2: custom2
  custom2: custom2
```

## Configuration Management

The project uses environment variables for configuration as per the existing setup.
- Provide sensible defaults where appropriate
- Document all configurable parameters clearly