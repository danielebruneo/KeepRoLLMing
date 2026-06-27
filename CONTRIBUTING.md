# Contributing

Thanks for your interest in KeepRoLLMing!

## Development Setup

```bash
git clone https://github.com/danielebruneo/KeepRoLLMing.git
cd KeepRoLLMing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## Running Tests

```bash
# Full suite
bash scripts/run-parallel-tests.sh

# Single test
bash scripts/run-single-test.sh tests/path/to/test.py
```

## Code Style

- Python 3.11+
- Run `ruff check .` before committing
- No trailing whitespace
- Type hints for all public APIs

## Pre-commit Checklist

- [ ] Tests pass (`pytest tests/ -q`)
- [ ] No ruff warnings (`ruff check .`)
- [ ] No `print()` in production code (use `logging.warning()`)
- [ ] CHANGELOG.md updated with your changes
