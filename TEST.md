# Testing

KeepRoLLMing uses a multi-layer testing strategy designed to keep behavior stable even during large refactors.

## Test levels

### 1. Unit tests
Fast tests for isolated logic such as:

- token estimation
- rolling summary chunking
- retry policies
- performance log aggregation
- context window logic

Run all tests with:

```bash
pytest
```

### 2. Integration tests
These validate interactions between internal modules without depending on a real backend.

They cover combinations like:

- orchestrator + rolling summary
- orchestrator + performance logging
- retry and overflow handling

### 3. End-to-end (E2E) tests
These tests spin up real HTTP servers and send real HTTP requests through the orchestrator.

They validate:

- HTTP routing
- streaming responses
- retry behavior
- rolling summary
- performance logging
- compatibility with OpenAI-style backends

The E2E suite can run in two modes.

#### Fake backend mode
A controllable OpenAI-compatible fake backend is started automatically.

It can simulate:

- streaming and non-stream responses
- missing usage metadata
- slow TTFT
- context overflow
- HTTP errors (400 / 500)
- interrupted streams

Run only E2E tests with:

```bash
pytest tests/e2e
```

Or only the fake E2E tests:

```bash
pytest -m e2e_fake
```

#### Live backend mode
The same E2E tests can also run against a real backend.

Set:

```bash
export E2E_LIVE_BASE_URL=http://localhost:1234
export E2E_LIVE_MODEL=qwen2.5-7b-instruct
export E2E_LIVE_SUMMARY_MODEL=qwen2.5-1.5b-instruct
```

Then run:

```bash
pytest -m e2e_live
```

If these variables are missing, live tests are skipped automatically.

Live tests focus on behavioral invariants rather than exact output text.

## Useful commands

Run everything:

```bash
pytest
```

Verbose:

```bash
pytest -v
pytest -vv
```

Show print output and uncaptured logs:

```bash
pytest -vv -s
```

Show Python logging output live:

```bash
pytest -vv --log-cli-level=INFO
pytest -vv --log-cli-level=DEBUG
```

Run a single test:

```bash
pytest tests/e2e/test_http_e2e.py::test_e2e_stream_roundtrip_and_live_metrics -vv -s
```

Stop at first failure:

```bash
pytest -x -vv
```

## Test architecture

The main E2E files are:

```text
tests/
├─ e2e/
│  ├─ fake_backend.py
│  ├─ conftest.py
│  └─ test_http_e2e.py
```

The harness automatically:

1. starts a fake OpenAI-compatible backend when needed
2. starts the KeepRoLLMing orchestrator
3. sends real HTTP requests
4. validates behavior and side effects

## Performance log verification

Some tests verify that the orchestrator writes:

```text
performance_logs/
├─ <model>.requests.yaml
└─ summary.yaml
```

These metrics include:

- TTFT
- TPS
- request timing
- aggregated statistics

## Why E2E tests matter

KeepRoLLMing sits between clients and real streaming LLM backends, so many important bugs are protocol and integration bugs rather than pure logic bugs.

E2E tests help guarantee that:

- protocol compatibility is preserved
- retry logic still works
- summary fallback logic stays safe
- refactors do not silently change behavior

## Dev dependencies

Async tests require the dev requirements.

Install them with:

```bash
pip install -r requirements-dev.txt
```

This includes `pytest-asyncio`, which is required for the async summary regression tests.
