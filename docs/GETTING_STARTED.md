# Getting started

## Install

KeepRoLLMing requires Python 3.11 or newer.

```bash
git clone https://github.com/danielebruneo/KeepRoLLMing.git
cd KeepRoLLMing
bash scripts/setup.sh
```

This creates `.venv` and installs the package in editable mode. To select a
specific interpreter, set `KRM_PYTHON`, for example
`KRM_PYTHON=python3.12 bash scripts/setup.sh`.

## Verify the proxy without an LLM

```bash
bash scripts/start-with-fake.sh
```

The script starts a fake upstream and the proxy. In another terminal:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"internal/fake","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

`internal/fake` intentionally enables `system_prompt`, `model_nudge`, and
`model_tool_loop_stopper` using the same canonical `filters:` syntax as a real
configuration.

## Connect a real backend

```bash
cp config.example.yaml config.yaml
# Edit base/local.model and base/local.upstream_url.
./krm serve --port 8000 --config config.yaml
```

Then send a request with `model: "chat/local"`. The proxy forwards it to the
configured upstream model. `code/assistant` is an example route that inherits
the same backend but enables a system prompt and nudge filter.

For all available settings, start from
[`config.example.full.yaml`](../config.example.full.yaml), not from an old
configuration snippet found in an issue or historical discussion.
