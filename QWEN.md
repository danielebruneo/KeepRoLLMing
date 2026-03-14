# Keeprollming Orchestrator

This project is a FastAPI proxy/orchestrator that sits in front of an OpenAI-compatible backend (e.g., LM Studio) and adds **rolling-summary** support to avoid context overflow.

## Overview

For detailed project overview, please refer to [PROJECT.md](./_project/PROJECT.md)

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

For detailed project structure information, please refer to [PROJECT.md](./_project/PROJECT.md)

The project uses the following directory structure:
- `_tasks/` - for collaboration and task management (ACTIVE_TASK.md, COMPLETED_TASKS.md, TODO.md, WORKFLOW.md)
- `_project/` - for project-level guidelines and conventions
- `_docs/` - for technical documentation
- `_memory/` - for important project notes and learnings worth remembering
- `_skills/` - for specialized project skills (REVIEW-DOC.md)
- Main code remains in `keeprollming/` and `tests/`

## Running

For running instructions, please refer to [RUNNING.md](./_docs/RUNNING.md).

## Tests

For test documentation and guidelines, please refer to [TESTING.md](./_docs/TESTING.md) or check the existing tests in `tests/` directory.

Notes:
- Tests are **unit/integration-ish** but do not require a live LM Studio instance: upstream calls are mocked.
- Some end-to-end tests may fail in parallel execution mode due to test infrastructure issues.
  To run all tests successfully, use: `pytest --tb=no -n0` or ensure the default configuration handles
  parallelization properly. The project is configured to automatically exclude problematic tests from
  parallel execution through custom markers.

## Configuration

For configuration details, please refer to [CONFIGURATION.md](./_docs/CONFIGURATION.md).

## Development Conventions

The project follows specific conventions and best practices which are documented in [CONVENTIONS.md](./_project/CONVENTIONS.md).

## Workflow Guidelines

For project workflow conventions and task management, please refer to [WORKFLOW.md](./_tasks/WORKFLOW.md) which contains detailed guidelines on:
- Active work tracking via ACTIVE_TASK.md
- Completed tasks archival in COMPLETED_TASKS.md
- Future task planning in TODO.md
- Overall project collaboration conventions

## Memory Management

For important project notes and learnings, please refer to [MEMORY.md](./_memory/MEMORY.md) which contains:
- Key insights and references worth remembering for future work
- Troubleshooting guides and best practices
- Implementation details that are valuable to preserve

## Project Guidelines

For project-level documentation and conventions, please refer to [PROJECT.md](./_project/PROJECT.md) which contains detailed information about:
- Overall project overview
- Versioning strategy and practices
- Coding conventions, best practices, etc.
- Configuration management guidelines

## Skills

This project includes specialized skills for working with project documentation. These skills can be invoked using the `skill` command in Qwen Code:

- **REVIEW-DOC**: Reviews all markdown files to ensure they accurately reflect the current status of the project and contain only relevant information. It consolidates knowledge by identifying missing information and moving irrelevant content to appropriate locations.

Skills are designed to help maintain high-quality, accurate documentation by ensuring that:
- All documentation reflects current implementation status
- Information is properly organized and consolidated
- Each document contains only relevant content
- Missing information is identified and addressed

To use a skill, simply run:
```
skill: "REVIEW-DOC"
```

## Qwen Added Memories
- Tests require the virtual environment to be set up before running them
- The project has been fully tested with both sequential (-n0) and parallel (-n4) test execution modes, all 39 tests now pass correctly. The main issue was fixed by resolving model name mismatch between orchestrator and fake backend recognition for summary calls.
