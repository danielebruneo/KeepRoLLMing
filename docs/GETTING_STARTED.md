# Getting Started

## Installation

```bash
git clone https://github.com/danielebruneo/KeepRoLLMing.git
cd KeepRoLLMing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## First Run (with fake backend)

```bash
bash scripts/start-with-fake.sh
```

This starts both a fake LLM backend and the orchestrator. Test it:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"internal/full","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

## Connecting Your LLM

```bash
export UPSTREAM_BASE_URL="http://127.0.0.1:1234"
python keeprollming.py --port 8000
```

The orchestrator automatically appends `/v1` to the upstream URL.

## Your First Config

Create `config.yaml`:

```yaml
models:
  my-model: {context_length: 32768}
routes:
  my-route:
    pattern: "my-model"
    model: "my-model"
    upstream_url: "http://127.0.0.1:1234"
    filter_chain:
      order: [system_prompt, model_nudge]
      filters:
        system_prompt:
          enabled: true
          prompt: "You are a helpful assistant."
        model_nudge:
          enabled: true
          trigger_patterns: [":$"]
```

Start with your config:

```bash
CONFIG_FILE=config.yaml python keeprollming.py --port 8000
```
