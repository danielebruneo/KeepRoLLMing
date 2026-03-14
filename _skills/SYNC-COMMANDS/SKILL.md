---
name: SYNC-COMMANDS
description: Refreshes _agent/COMMANDS.md from the actual repository scripts, tooling, and test commands.
---

# SYNC-COMMANDS Skill

## Goal
Populate or refresh [_agent/COMMANDS.md](../../_agent/COMMANDS.md) from the real repository.

## Procedure
1. Inspect package files, Makefiles, scripts, and CI config if present - identify all available commands.
2. Extract the real setup, run, test, lint, and typecheck commands - capture what actually works.
3. Keep the command list short and practical - avoid overly complex or rarely-used commands.

## When to Use
- When updating command documentation after changes to scripts or setup
- When preparing for new agent session with current repository state
- When ensuring command documentation matches actual environment

## Examples
- Updating commands after adding new test scripts (e.g., adding pytest commands to COMMANDS.md)
- Refreshing command list after modifying Makefiles (e.g., updating run commands based on new Makefile targets)
- Creating clean command listing from existing project tools (e.g., extracting npm run commands from package.json)