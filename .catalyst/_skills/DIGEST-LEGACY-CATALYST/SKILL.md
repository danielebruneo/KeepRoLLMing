---
name: DIGEST-LEGACY-CATALYST
description: Digest archived legacy CATALYST assets under .catalyst_legacy and redistribute information units into the new layered structure.
---

# DIGEST-LEGACY-CATALYST

## Goal
Digest archived legacy assets under `.catalyst_legacy/` and redistribute **information units** into the new layered structure.

## Important principle
Do not migrate file-to-file blindly.
Extract information pieces and route them semantically.
When digesting legacy content:
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

## Required behavior
If `.catalyst_legacy/LATEST_MIGRATION.md` exists, do **not** conclude "nothing to do" merely because the new layered structure already exists.
Inspect the archived snapshot itself.

## Legacy skill handling
Legacy skills require special handling:
1. Inspect each legacy skill directory.
2. Determine whether the legacy skill is:
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

## Enhanced Procedure 
1. **Read `.catalyst_legacy/LATEST_MIGRATION.md`** and identify the latest migration snapshot.
2. **Inspect the actual archived snapshot directory** even if the new layered structure already exists.
3. **Review legacy agent/project/core-like files**, paying special attention to:
   - Distinguish between project-specific content (host repository description) vs. agent workflow knowledge
   - Identify when files describe the system itself vs. how the agent operates on it  
4. **Extract information units, not just files**.
5. **Route units properly based on semantic meaning**:
   - Project-specific architecture/components/constraints/tests → `_project/`
   - Agent thinking/remembering/handoff/skill choice behavior → `_agent/` 
6. **Use `RECONCILE-LEGACY-SKILLS` for legacy skill handling** and ensure proper classification.
7. **Record unresolved ambiguities in `_agent/self/LEGACY_DIGEST_NOTES.md`** with explicit reasoning about the decision made.
8. **Archive anything still unclear rather than forcing a bad import** - especially when semantic meaning is ambiguous.
9. **Run `SYNC-QWEN-SKILL-REGISTRY` once skill inventory changes are finalized.**

## Output
- A concise digest report 
- Updated destinations in the new layered structure  
- Updated `_agent/self/LEGACY_DIGEST_NOTES.md` with explicit reasoning about decisions made
- Triggered or recommended registry sync

## Improvement Notes
This skill has been enhanced to emphasize:
1. **Semantic routing** rather than location-based file copying 
2. **Explicit classification logic** for distinguishing project vs agent content  
3. **Process consistency** - ensure template, datetime tracking, and cross-referencing are applied uniformly
