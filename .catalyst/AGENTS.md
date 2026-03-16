# CATALYST Core AGENTS

This repository uses a layered CATALYST architecture.

## Layers
- `.catalyst/` = core workflow, core skills, templates, and core docs
- `_agent/` = runtime state, self-brain, local overlays, and learning reports
- `_project/` = project-specific knowledge, docs, and skills
- `_docs/` = root human / technical documentation in the host project
- `.qwen/skills/` = runtime projection registry for Qwen

## Critical runtime rule
Skills are repository-side **markdown procedures**.
They are **not** Python executables.
Do **not** attempt to run `main.py` inside `.qwen/skills/`.

## Skill authoring locations
Skills may live in:
1. `_agent/overlay/_skills/`
2. `_project/_skills/`
3. `.catalyst/_skills/`

## Runtime skill visibility
Qwen reads skills from `.qwen/skills/`.
That directory must be treated as a generated runtime projection, not as the canonical store.
Use `SYNC-QWEN-SKILL-REGISTRY` whenever skill inventory changes.

## Reading order
1. `_agent/state/SCOPE.md`
2. `_agent/state/ACTIVE_TASK.md`
3. `_agent/state/HANDOFF.md`
4. `_agent/self/MEMORY.md`
5. `_project/KNOWLEDGE_BASE.md` and `_project/MAP.md` when relevant
6. Relevant root docs under `_docs/`

## Cognitive loop
1. THINK
2. PLAN (when needed)
3. WORK
4. FEEDBACK
5. LEARN
6. ADAPT (small and safe only)
7. CLOSE-TASK

## Setup / upgrade loop
1. deterministic setup installs the new core and archives legacy assets
2. `DIGEST-LEGACY-CATALYST` semantically redistributes legacy information
3. `RECONCILE-LEGACY-SKILLS` classifies and consolidates legacy skills
4. `SYNC-QWEN-SKILL-REGISTRY` refreshes runtime visibility

## Human docs boundary
CATALYST should not overwrite the host project's `README.md` during setup.
Human-facing project docs belong to the host project, typically in root and `_docs/`.
