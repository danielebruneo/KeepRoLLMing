# AGENTS.md

## Purpose
[CATALYST](CATALYST.md) is the canonical workflow layer for agent-assisted development in this repository.
It keeps task scope, memory, documentation, and self-improvement aligned.

If you arrived here through [QWEN.md](QWEN.md), continue with the CATALYST workflow below.

## Canonical bootstrap sources
Use these files with distinct roles:
- [QWEN.md](QWEN.md): runner-specific bootstrap entrypoint for Qwen Code
- [AGENTS.md](AGENTS.md): canonical agent workflow and operating rules
- [README.md](README.md): human/public project overview and contributor-facing guidance

Do not remove CATALYST references from [QWEN.md](QWEN.md) or the agent-assistance section from [README.md](README.md) without explicit intent.

## Reading order
When starting work in this repository, read in this order:
1. [_agent/state/ACTIVE_TASK.md](_agent/state/ACTIVE_TASK.md)
2. [_agent/state/HANDOFF.md](_agent/state/HANDOFF.md)
3. [_agent/knowledge/CONSTRAINTS.md](_agent/knowledge/CONSTRAINTS.md)
4. [_agent/knowledge/DONE_CRITERIA.md](_agent/knowledge/DONE_CRITERIA.md)
5. [_agent/knowledge/KNOWLEDGE_BASE.md](_agent/knowledge/KNOWLEDGE_BASE.md)
6. [_agent/knowledge/MAP.md](_agent/knowledge/MAP.md)
7. [_agent/COMMANDS.md](_agent/COMMANDS.md)
8. Relevant docs under [_docs/](_docs/)

## Operating rules
- Work only on the active task unless the user explicitly changes priority.
- Prefer minimal, reversible changes.
- Do not refactor unrelated code.
- Do not rewrite working logic just to make it cleaner.
- Before editing, identify the files most likely involved and explain why.
- Run the smallest relevant test set first, then broader tests if needed.
- Update [_agent/HANDOFF.md](_agent/HANDOFF.md) before ending the session.
- Add non-obvious lessons to [_agent/MEMORY.md](_agent/MEMORY.md).
- Treat `SKILL.md` as canonical in every skill directory. If a `SKILL-<NAME>.md` path exists, treat it as an alias/symlink path.

## Runtime boundary
- Use the tools provided by the runtime according to their actual schema.
- Do not infer or redefine tool APIs inside repository documentation.
- Prefer runtime-provided tool contracts over repository-local assumptions.

## Bootstrap maintenance
CATALYST uses bootstrap redundancy:
- [QWEN.md](QWEN.md) should remain a thin runner-specific loader.
- [AGENTS.md](AGENTS.md) is the canonical workflow specification.
- [README.md](README.md) should include a short agent-assisted development section for humans and agents that pass through it.

When bootstrap files drift or are regenerated, use [SYNC-BOOTSTRAP-FILES](_skills/SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md).

## Skill usage
Use the matching skill whenever applicable:
- [CREATE-ACTIVE-TASK](_skills/CREATE-ACTIVE-TASK/SKILL-CREATE-ACTIVE-TASK.md)
- [BUILD-REPO-MAP](_skills/BUILD-REPO-MAP/SKILL-BUILD-REPO-MAP.md)
- [INIT-KNOWLEDGE-BASE](_skills/INIT-KNOWLEDGE-BASE/SKILL-INIT-KNOWLEDGE-BASE.md)
- [UPDATE-KNOWLEDGE-BASE](_skills/UPDATE-KNOWLEDGE-BASE/SKILL-UPDATE-KNOWLEDGE-BASE.md)
- [UPDATE-HUMAN-DOCS](_skills/UPDATE-HUMAN-DOCS/SKILL-UPDATE-HUMAN-DOCS.md)
- [UPDATE-README](_skills/UPDATE-README/SKILL-UPDATE-README.md)
- [SYNC-BOOTSTRAP-FILES](_skills/SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md)
- [FIX-FAILING-TEST](_skills/FIX-FAILING-TEST/SKILL-FIX-FAILING-TEST.md)
- [ADD-FEATURE](_skills/ADD-FEATURE/SKILL-ADD-FEATURE.md)
- [SAFE-REFACTOR](_skills/SAFE-REFACTOR/SKILL-SAFE-REFACTOR.md)
- [REVIEW-DOC](_skills/REVIEW-DOC/SKILL-REVIEW-DOC.md)
- [FEEDBACK](_skills/FEEDBACK/SKILL-FEEDBACK.md)
- [IMPLEMENT-FEEDBACK](_skills/IMPLEMENT-FEEDBACK/SKILL-IMPLEMENT-FEEDBACK.md)
- [LEARN](_skills/LEARN/SKILL-LEARN.md)
- [CLOSE-TASK](_skills/CLOSE-TASK/SKILL-CLOSE-TASK.md)
- [SYNC-COMMANDS](_skills/SYNC-COMMANDS/SKILL-SYNC-COMMANDS.md)

## Conflict resolution
If instructions conflict, use this order:
1. User request
2. [_agent/ACTIVE_TASK.md](_agent/ACTIVE_TASK.md)
3. [_agent/CONSTRAINTS.md](_agent/CONSTRAINTS.md)
4. Existing tests
5. Architecture docs
