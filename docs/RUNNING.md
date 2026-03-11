# Running the Orchestrator

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Starting the Server

```bash
export UPSTREAM_BASE_URL="http://127.0.0.1:1234"   # LM Studio base (no /v1)
uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000
```

## Making Requests

Example request:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"local/main","messages":[{"role":"user","content":"ciao"}]}'}'
```

## Commands

- Run tests: `pytest`
- Run server: `uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000`