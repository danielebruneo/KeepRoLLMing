---
name: BUILD-REPO-MAP
description: Builds or refreshes _agent/MAP.md by inferring the real repository structure and responsibilities.
---

# BUILD-REPO-MAP Skill

## Goal
Populate [`_project/MAP.md`](../../_project/MAP.md) with a concise navigation map of the real repository.

## Procedure
1. Inspect the repository tree to understand project structure.
2. Identify core runtime areas, test areas, docs, and entry points.
3. Summarize each area in one short line for easy scanning.
4. Use standard markdown links to concrete files or folders where useful.
5. Keep the map compact and scannable - avoid overly detailed descriptions.

## When to Use
- When initializing a new agent session in the repository
- After major project changes that affect structure
- When trying to understand the project layout for new tasks

## Examples
- Creating initial repository map when starting work
- Updating the map after adding new modules or file
- Refreshing the map when restructuring project directories

## Template Reference
This skill follows the [ACTIVE_TASK.template.md](../../_templates/ACTIVE_TASK.template.md) template format for consistency in documentation structure.

## Documentation Cross-reference
- [_agent/KNOWLEDGE_BASE.md](../../_agent/KNOWLEDGE_BASE.md): Project knowledge base
- [_docs/architecture/OVERVIEW.md](../../_docs/architecture/OVERVIEW.md): Architecture overview
