---
name: ADD-FEATURE
description: Implements a requested feature in a controlled, test-aware way without broad unrelated changes.
---

# ADD-FEATURE Skill

## Goal
Implement a requested feature in a controlled, test-aware way that maintains project stability.

## Procedure
1. Create or refresh [_agent/ACTIVE_TASK.md](../../_agent/ACTIVE_TASK.md) with a clear, focused task definition.
2. Identify the minimal set of modules involved - avoid touching unrelated code.
3. Implement the feature with minimal collateral change - focus on core functionality only.
4. Add or update tests if appropriate - ensure test coverage for new functionality.
5. Record the outcome in [_agent/HANDOFF.md](../../_agent/HANDOFF.md) with clear summary of changes.

## When to Use
- When implementing a new feature requested by user
- When adding functionality that requires tests
- When the change should be minimal and focused

## Examples
- Adding support for a new model profile (e.g., adding a new profile in config.yaml)
- Implementing streaming response handling (e.g., modifying app.py to support SSE responses)
- Adding configuration options for new parameters (e.g., adding environment variables for new settings)
