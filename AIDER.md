# Aider project notes

## Test conventions

- Assert failures should print response text:
  - `assert resp.status_code == 200, resp.text`
- Streaming tests should check:
  - `content-type: text/event-stream`
  - presence of `data:` and `[DONE]`