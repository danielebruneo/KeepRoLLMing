# Contributing to KeepRoLLMing

Thank you for helping improve KeepRoLLMing. The public repository contains the
code, tests, examples, and current operational documentation needed for a
contribution; internal planning and historical investigations are deliberately
kept out of releases.

## Development setup

Python 3.11 or newer is required.

```bash
git clone https://github.com/danielebruneo/KeepRoLLMing.git
cd KeepRoLLMing
bash scripts/setup.sh --dev
```

This creates a project-local `.venv` and installs the package editable with
test and lint dependencies. Equivalent direct command:

```bash
.venv/bin/python -m pip install -e ".[dev]"
```

## Verify a change

```bash
# Focused test file, optionally with normal pytest arguments.
bash scripts/run-single-test.sh tests/filters/test_registry.py

# Full automated suite: parallel unit/functional tests, then serial E2E tests.
bash scripts/run-parallel-tests.sh

# Static checks for the files changed by your contribution.
.venv/bin/ruff check keeprollming/filters/your_module.py tests/filters/test_your_module.py
```

Use `bash scripts/start-with-fake.sh` when you need to observe a real proxy and
upstream exchange without a local model.

## Project boundaries

- `keeprollming/filters/` contains community-facing built-in filter modules.
  Each module owns request logic, optional stream logic, and a configuration
  schema; use an existing module as a pattern.
- `keeprollming/filters/registry.py` is the canonical built-in catalogue.
- `tests/` mirrors feature areas. Add regression coverage at the narrowest
  useful level and add E2E coverage for public HTTP/streaming contracts.
- `docs/` is public, current operational documentation. Keep it aligned with
  `config.example.yaml` and `config.example.full.yaml`.

## Pull requests

Before opening a pull request:

- Explain the user-visible motivation and any compatibility effect.
- Keep configuration keys and docs consistent with the registry/loader.
- Include focused tests; run the relevant suite.
- Run Ruff on changed Python files and avoid unrelated formatting or generated artifacts.
- Update `CHANGELOG.md` when the change matters to users or operators.

Please report security-sensitive issues privately rather than placing secrets,
raw request captures, or credentials in a public issue.
