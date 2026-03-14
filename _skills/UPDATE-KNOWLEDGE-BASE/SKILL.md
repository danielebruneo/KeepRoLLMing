---
name: UPDATE-KNOWLEDGE-BASE
description: Refreshes the internal agent knowledge files after meaningful project changes.
---

# UPDATE-KNOWLEDGE-BASE Skill

## Goal
Refresh the internal knowledge files after the project changed.

## Procedure
1. Compare the current repository state with the existing docs - identify what's changed.
2. Update only the parts that are now stale - avoid unnecessary modifications.
3. Keep architecture docs durable and avoid task noise - focus on structural elements.
4. Keep [_agent/MAP.md](../../_agent/MAP.md) and [_agent/COMMANDS.md](../../_agent/COMMANDS.md) aligned with reality - ensure they reflect current state.

## When to Use
- After project structure or code changes that affect internal documentation
- When repository has been modified in ways that impact knowledge base
- When maintaining consistency between code and documentation

## Examples
- Updating MAP after adding new modules to the project
- Refreshing COMMANDS when new scripts are added
- Aligning knowledge files with recent code refactoring