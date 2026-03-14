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

- Run tests: `pytest` or better yet, use the dedicated script: `./run-tests.sh`
- Run individual test: `./run-single-test.sh test_name` 
- Run server: `uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000`

## Test Environment Notes

Due to compatibility issues with pytest dependencies that were causing warnings and test failures, we recommend using:
```bash
./run-tests.sh
```
This script creates a clean virtual environment for running tests, ensuring proper dependency resolution.

## Date Reference
This document was last reviewed for accuracy: sabato 14 marzo 2026