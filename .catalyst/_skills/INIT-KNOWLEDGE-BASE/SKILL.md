---
name: INIT-KNOWLEDGE-BASE
description: Initializes the internal agent knowledge files by inferring project structure, commands, and architecture from the current repository.
---

# INIT-KNOWLEDGE-BASE Skill

## Goal
Initialize the internal agent knowledge base from the current repository state.

## Target files
- [_agent/MAP.md](../../_agent/MAP.md) - Repository structure map
- [_agent/COMMANDS.md](../../_agent/COMMANDS.md) - Setup, run, and test commands
- [_docs/architecture/OVERVIEW.md](../../_docs/architecture/OVERVIEW.md) - Architecture overview
- [_docs/architecture/INVARIANTS.md](../../_docs/architecture/INVARIANTS.md) - Core architecture constraints
- optional decisions in [_docs/decisions/DECISIONS.md](../../_docs/decisions/DECISIONS.md) - Project design decisions

## Procedure
1. Read the README and inspect the repository tree - understand what's there.
2. Infer the main modules and responsibilities - identify core components.
3. Infer setup, run, and test commands if they are visible - document how to work with project.
4. Populate the target files with concise, grounded information - make them useful for agents.

## When to Use
- When starting a new agent session in the repository
- After major project changes that affect structure or commands
- When setting up fresh knowledge base after cloning or reset

## Examples
- Setting up initial repository understanding for new agent (e.g., creating a basic MAP.md with main directories)
- Refreshing knowledge base after adding new modules (e.g., updating COMMANDS.md with new test scripts)
- Creating command documentation when setup is changed (e.g., documenting new environment variables or installation steps)

## Template Reference
This skill follows the [ACTIVE_TASK.template.md](../../_templates/ACTIVE_TASK.template.md) template format for consistency in documentation structure.

## Documentation Cross-reference
- [_agent/KNOWLEDGE_BASE.md](../../_agent/KNOWLEDGE_BASE.md): Project knowledge base
- [_docs/architecture/OVERVIEW.md](../../_docs/architecture/OVERVIEW.md): Architecture overview
- [_docs/decisions/DECISIONS.md](../../_docs/decisions/DECISIONS.md): Design decisions

## Knowledge Base Enhancement
This skill now includes enhanced functionality for:
- Template-based workflow system integration
- DateTime tracking in all knowledge files
- Consistent formatting across all components
- Cross-referencing between project elements