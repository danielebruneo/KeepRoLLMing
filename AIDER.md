# Aider project notes

## Golden rules

- Keep diffs small: one feature/fix per change.
- Prefer adding/updating tests over refactoring.
- Never generate dozens of near-identical tests:
  - Use `pytest.mark.parametrize` instead.
- Tests must not require a live upstream (LM Studio):
  - Mock `keeprollming.app.http_client` in tests.
- If changing config/env behaviour:
  - avoid reading env at import-time when possible, or document it.

## Commands

- Run tests: `pytest`
- Run server: `uvicorn keeprollming.app:app --host 0.0.0.0 --port 8000`

## Test conventions

- Assert failures should print response text:
  - `assert resp.status_code == 200, resp.text`
- Streaming tests should check:
  - `content-type: text/event-stream`
  - presence of `data:` and `[DONE]`
