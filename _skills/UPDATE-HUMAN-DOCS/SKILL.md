---
name: UPDATE-HUMAN-DOCS
description: Updates human-facing project documentation without mixing it with internal agent operational notes.
---

# UPDATE-HUMAN-DOCS Skill

## Goal
Update public, human-facing docs while keeping internal agent knowledge separate.

## Scope
Human-facing docs typically include:
- [README.md](CATALYST.md)
- files under [_docs/](../../_docs/)

## Rules
- Do not dump internal task state into human docs - keep focus on user needs.
- Prefer clear summaries over implementation diaries - make documentation accessible.
- Keep architecture, workflow, and usage docs understandable to humans first - prioritize clarity.

## When to Use
- When updating README or other public documentation
- When adding usage instructions for users
- When improving project documentation for external audiences

## Examples
- Updating README with new features or usage examples
- Adding configuration details to documentation files
- Improving installation instructions in public docs