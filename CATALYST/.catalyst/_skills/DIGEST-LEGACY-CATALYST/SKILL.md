---
name: DIGEST-LEGACY-CATALYST
description: Digest archived legacy CATALYST assets under .catalyst_legacy and redistribute information units into the new layered structure.
---

# DIGEST-LEGACY-CATALYST

## Goal
Digest archived legacy assets under `.catalyst_legacy/` and redistribute **information units** into the new layered structure.

## Preliminary note
This is an high reasoning task, go step by step, use THINK, REFLECT, ADAPT if needed.
Follow the Procedure with diligence.

## Important principle
Do not migrate file-to-file blindly.
Think before copyng a whole file and replacing or overriding a CATALYST provided file; prefer integrating legacy information into existing files.
Extract information pieces and route them semantically.
When digesting legacy content:
- Classify every piece of content
- Move project-specific knowledge to _project/
- Move agent workflow knowledge to _agent/
If a piece of information describes how the agent should think,
remember, handoff, or choose skills → AGENT.
If it describes the host repository architecture,
components, constraints or tests → PROJECT.

## Runtime note
This is a markdown skill, not a Python executable.
Do **not** try to run `main.py` inside `.qwen/skills/`.

## Required destinations
- `_agent/state/`
- `_agent/self/`
- `_agent/overlay/`
- `_project/`
- `_docs/`
- `_agent/self/CORE_CHANGE_CANDIDATES.md` when appropriate
Refer to CATALYST.md to understand what each location is supposed to contain.
Make sure also to understand how the different catalyst core / agent / project layers can integrate and override each other, while mantaining proper separation of scope.

## Required behavior
If `.catalyst_legacy/LATEST_MIGRATION.md` exists, do **not** conclude "nothing to do" merely because the new layered structure already exists.
Inspect the archived snapshot itself.

## Legacy skill handling
Legacy skills require special handling:
1. Inspect each legacy skill directory.
2. For each skill think and determine whether the legacy skill is:
   - `core-equivalent`
   - `overlay-candidate`
   - `project-skill`
   - `core-candidate`
   - `archive-only`
3. Compare with existing core and project skills.
4. Consolidate where possible instead of copying duplicates.
5. Preserve canonical skill convention:
   - `SKILL.md` is canonical
   - `SKILL-<NAME>.md` is alias/symlink
6. Use `RECONCILE-LEGACY-SKILLS` for skill-specific reconciliation.
7. After skill digestion, run `SYNC-QWEN-SKILL-REGISTRY`.

## Procedure
1. Read `.catalyst_legacy/LATEST_MIGRATION.md` and identify the latest migration snapshot.
2. Inspect the actual archived snapshot directory even if the new layered structure already exists.
3. Review legacy agent/project/core-like files.
4. Extract information units, not just files.
5. Route units to the proper new destinations.
6. Use `RECONCILE-LEGACY-SKILLS` for legacy skill handling.
7. Record unresolved ambiguities in `_agent/self/LEGACY_DIGEST_NOTES.md`.
8. Archive anything still unclear rather than forcing a bad import.
9. Run `SYNC-QWEN-SKILL-REGISTRY` once skill inventory changes are finalized.
10. Commit your changes with a "Legacy migraton attempt [datetime]" message
11. Review what changed with your commit and analyze if your changes actually followed rules and accomplished goal.
12. If you're happy with your changes, produce a migration log and commit with a "Legacy migration succesfull [datetime]" message, otherwise roll back LEARN (and ADAPT if needed) and try again from the beginning till success

## Output
- Transfer of knowledge consolidated from the Legacy Knowledgebase to the new structure
- A concise digest report
- Updated destinations in the new layered structure
- Updated `_agent/self/LEGACY_DIGEST_NOTES.md`
- Triggered or recommended registry sync
