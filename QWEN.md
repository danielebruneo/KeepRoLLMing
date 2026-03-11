# Keeprollming Orchestrator

This project is a FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds **rolling-summary** support to avoid context overflow.

## Overview

For detailed project overview, please refer to [PROJECT.md](./project/PROJECT.md)

## Features

- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- Profiles:
  - `local/quick`
  - `local/main`
  - `local/deep`
- Passthrough mode:
  - `pass/<BACKEND_MODEL_NAME>` (routes directly, **no summarization**)
- Streaming proxy (SSE) support
- Best-effort token accounting

## Project Structure

For detailed project structure information, please refer to [PROJECT.md](./project/PROJECT.md)

## Running

For running instructions, please refer to [RUNNING.md](./docs/RUNNING.md).

## Tests

For test documentation and guidelines, please refer to [TESTING.md](./docs/TESTING.md) or check the existing tests in `tests/` directory.

Notes:
- Tests are **unit/integration-ish** but do not require a live LM Studio instance: upstream calls are mocked.
- Some end-to-end tests may fail in parallel execution mode due to test infrastructure issues.
  To run all tests successfully, use: `pytest --tb=no -n0` or ensure the default configuration handles
  parallelization properly. The project is configured to automatically exclude problematic tests from
  parallel execution through custom markers.

## Configuration

For configuration details, please refer to [CONFIGURATION.md](./docs/CONFIGURATION.md).

## Development Conventions

The project follows specific conventions and best practices which are documented in [CONVENTIONS.md](./project/CONVENTIONS.md).

## Workflow Guidelines

For project workflow conventions and task management, please refer to [WORKFLOW.md](./workflow/WORKFLOW.md) which contains detailed guidelines on:
- Active work tracking via ACTIVE_TASK.md
- Completed tasks archival in COMPLETED_TASKS.md
- Future task planning in TODO.md
- Overall project collaboration conventions

## Project Guidelines

For project-level documentation and conventions, please refer to [PROJECT.md](./project/PROJECT.md) which contains detailed information about:
- Overall project overview
- Versioning strategy and practices
- Coding conventions, best practices, etc.
- Configuration management guidelines