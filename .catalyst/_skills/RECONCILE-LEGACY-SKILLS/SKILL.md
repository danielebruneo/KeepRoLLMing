---
name: RECONCILE-LEGACY-SKILLS
description: Analyze legacy skills and reconcile them with the new layered CATALYST skill model.
---

# RECONCILE-LEGACY-SKILLS

## Goal
Inspect legacy skills from `.catalyst_legacy/` and decide whether each should become a core-equivalent, overlay-candidate, project-skill, core-candidate, or archive-only artifact.

## Procedure
1. Enumerate legacy skill directories from the archived snapshot.
2. Compare each legacy skill against `.catalyst/_skills/` and `_project/_skills/`.
3. Classify each skill as one of:
   - `core-equivalent`
   - `overlay-candidate`
   - `project-skill`
   - `core-candidate`
   - `archive-only`
4. Prefer consolidation and interlinking over duplication.
5. If a legacy skill overlaps with an existing core skill, decide whether:
   - existing core skill is sufficient
   - local overlay is needed
   - project-specific skill should exist
   - a core promotion candidate should be recorded
6. Preserve canonical skill structure:
   - `SKILL.md` canonical
   - `SKILL-<NAME>.md` alias/symlink
7. Write ambiguous cases to `_agent/self/LEGACY_DIGEST_NOTES.md`.
8. Recommend or invoke `SYNC-QWEN-SKILL-REGISTRY` if visible skill inventory changes.
