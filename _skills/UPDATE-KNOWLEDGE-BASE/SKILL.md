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
- Updating MAP after adding new modules to the project (e.g., adding new directory entries to MAP.md)
- Refreshing COMMANDS when new scripts are added (e.g., updating COMMANDS.md with new test or build scripts)
- Aligning knowledge files with recent code refactoring (e.g., updating MAP.md to reflect new module locations)

## Knowledge Base Enhancement
This skill now includes enhanced functionality for:
- Template-based workflow system integration
- DateTime tracking in all knowledge files  
- Consistent formatting across all components
- Cross-referencing between project elements