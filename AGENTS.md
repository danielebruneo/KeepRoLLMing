# AGENTS.md

## Purpose
[CATALYST] is a lightweight repository-side control layer for coding agents.
It helps the agent keep context, task scope, memory, and documentation aligned.
You are a CATALYST Agent. Follow this guidelines.

## Reading order
When starting work in this repository, read in this order:
1. [_agent/ACTIVE_TASK.md](_agent/ACTIVE_TASK.md)
2. [_agent/HANDOFF.md](_agent/HANDOFF.md)
3. [_agent/CONSTRAINTS.md](_agent/CONSTRAINTS.md)
4. [_agent/DONE_CRITERIA.md](_agent/DONE_CRITERIA.md)
5. [_agent/MAP.md](_agent/MAP.md)
6. [_agent/COMMANDS.md](_agent/COMMANDS.md)
7. Relevant docs under [_docs/](_docs/)

## Operating rules
- Work only on the active task unless the user explicitly changes priority.
- Prefer minimal, reversible changes.
- Do not refactor unrelated code.
- Do not rewrite working logic just to make it cleaner.
- Before editing, identify the files most likely involved and explain why.
- Run the smallest relevant test set first, then broader tests if needed.
- Update [_agent/HANDOFF.md](_agent/HANDOFF.md) before ending the session.
- Add non-obvious lessons to [_agent/MEMORY.md](_agent/MEMORY.md).

## Runtime boundary
- Use the tools provided by the runtime according to their actual schema.
- Do not infer or redefine tool APIs inside repository documentation.
- Prefer runtime-provided tool contracts over repository-local assumptions.

## Skill usage
Use the matching skill whenever applicable:
- [CREATE-ACTIVE-TASK](_skills/CREATE-ACTIVE-TASK/SKILL-CREATE-ACTIVE-TASK.md)
- [BUILD-REPO-MAP](_skills/BUILD-REPO-MAP/SKILL-BUILD-REPO-MAP.md)
- [INIT-KNOWLEDGE-BASE](_skills/INIT-KNOWLEDGE-BASE/SKILL-INIT-KNOWLEDGE-BASE.md)
- [UPDATE-KNOWLEDGE-BASE](_skills/UPDATE-KNOWLEDGE-BASE/SKILL-UPDATE-KNOWLEDGE-BASE.md)
- [UPDATE-HUMAN-DOCS](_skills/UPDATE-HUMAN-DOCS/SKILL-UPDATE-HUMAN-DOCS.md)
- [UPDATE-README](_skills/UPDATE-README/SKILL-UPDATE-README.md)
- [FIX-FAILING-TEST](_skills/FIX-FAILING-TEST/SKILL-FIX-FAILING-TEST.md)
- [ADD-FEATURE](_skills/ADD-FEATURE/SKILL-ADD-FEATURE.md)
- [SAFE-REFACTOR](_skills/SAFE-REFACTOR/SKILL-SAFE-REFACTOR.md)
- [REVIEW-DOC](_skills/REVIEW-DOC/SKILL-REVIEW-DOC.md)
- [CLOSE-TASK](_skills/CLOSE-TASK/SKILL-CLOSE-TASK.md)
- [SYNC-COMMANDS](_skills/SYNC-COMMANDS/SKILL-SYNC-COMMANDS.md)
- [FEEDBACK](_skills/FEEDBACK/SKILL-FEEDBACK.md) - Analyzes workflows for learning improvements (not direct code changes)

## Conflict resolution
If instructions conflict, use this order:
1. User request
2. [_agent/ACTIVE_TASK.md](_agent/ACTIVE_TASK.md)
3. [_agent/CONSTRAINTS.md](_agent/CONSTRAINTS.md)
4. Existing tests
5. Architecture docs
