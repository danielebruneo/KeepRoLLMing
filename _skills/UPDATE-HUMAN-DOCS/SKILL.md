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
- Do not dump internal task state into human docs.
- Prefer clear summaries over implementation diaries.
- Keep architecture, workflow, and usage docs understandable to humans first.
