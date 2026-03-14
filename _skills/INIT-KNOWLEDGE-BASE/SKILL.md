---
name: INIT-KNOWLEDGE-BASE
description: Initializes the internal agent knowledge files by inferring project structure, commands, and architecture from the current repository.
---

# INIT-KNOWLEDGE-BASE Skill

## Goal
Initialize the internal agent knowledge base from the current repository state.

## Target files
- [_agent/MAP.md](../../_agent/MAP.md)
- [_agent/COMMANDS.md](../../_agent/COMMANDS.md)
- [_docs/architecture/OVERVIEW.md](../../_docs/architecture/OVERVIEW.md)
- [_docs/architecture/INVARIANTS.md](../../_docs/architecture/INVARIANTS.md)
- optional decisions in [_docs/decisions/DECISIONS.md](../../_docs/decisions/DECISIONS.md)

## Procedure
1. Read the README and inspect the repository tree.
2. Infer the main modules and responsibilities.
3. Infer setup, run, and test commands if they are visible.
4. Populate the target files with concise, grounded information.
