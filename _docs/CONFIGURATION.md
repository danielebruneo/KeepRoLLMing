# Configuration

## Environment Variables

- `UPSTREAM_BASE_URL` (default `http://127.0.0.1:1234/v1` is accepted, but recommended to provide without `/v1`)
- `MAIN_MODEL`, `SUMMARY_MODEL`
- `QUICK_MAIN_MODEL`, `QUICK_SUMMARY_MODEL`
- `BASE_MAIN_MODEL`, `BASE_SUMMARY_MODEL`
- `DEEP_MAIN_MODEL`, `DEEP_SUMMARY_MODEL`
- `MAX_HEAD`, `MAX_TAIL` (rolling-summary head/tail caps)
- `DEFAULT_CTX_LEN` - Default context length when no model info is available
- `SUMMARY_MAX_TOKENS` - Maximum tokens for summary generation

## Golden Rules

- Keep diffs small: one feature/fix per change.
- Prefer adding/updating tests over refactoring.
- Never generate dozens of near-identical tests:
  - Use `pytest.mark.parametrize` instead.
- Tests must not require a live upstream (LM Studio):
  - Mock `keeprollming.app.http_client` in tests.
- If changing config/env behaviour:
  - avoid reading env at import-time when possible, or document it.

## Configuration Management

The project uses environment variables for configuration as per the existing setup. 
- Provide sensible defaults where appropriate
- Document all configurable parameters clearly