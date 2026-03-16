---
name: SYNC-QWEN-SKILL-REGISTRY
description: Rebuild .qwen/skills as a runtime projection of active markdown skills from overlay, project, and core locations.
---

# SYNC-QWEN-SKILL-REGISTRY

## Goal
Rebuild `.qwen/skills/` as a clean runtime projection of active markdown skills that live elsewhere.

## Important principles
- `.qwen/skills/` is **not** a source-of-truth skill store.
- Skills are **markdown procedures**, not Python executables.
- Do **not** try to run `main.py` from `.qwen/skills/`.

## Canonical skill locations
1. `_agent/overlay/_skills/`
2. `_project/_skills/`
3. `.catalyst/_skills/`

## Resolution order
For each skill name, select the source in this precedence order:
1. `_agent/overlay/_skills/<NAME>/SKILL.md`
2. `_project/_skills/<NAME>/SKILL.md`
3. `.catalyst/_skills/<NAME>/SKILL.md`

## Procedure
1. Inspect available skills in overlay, project, and core locations.
2. Resolve the winning source for each skill name according to precedence.
3. Recreate `.qwen/skills/` as a clean **directory**, even if it used to be a symlink.
4. For each visible skill, materialize:
   - `.qwen/skills/<NAME>/SKILL.md` -> symlink to canonical source
   - `.qwen/skills/<NAME>/SKILL-<NAME>.md` -> symlink to `SKILL.md`
5. Remove stale runtime skill entries.
6. Produce a short report listing visible skills and shadowed sources.

## When to use
- After adding, removing, or moving any skill
- After editing any skill under core, project, or overlay
- After `DIGEST-LEGACY-CATALYST`
- After setup or upgrades
- After changes noticed in git under skill locations
