---
name: ADAPT
description: Apply a small, safe, local improvement to the CATALYST workflow after clear feedback or learning.
---

# ADAPT Skill

## Goal
Implement a minimal, low-risk improvement to CATALYST when a repeated friction point or clear lesson justifies a concrete workflow change.

## When to Use
Use ADAPT only when:
- a concrete issue has already been identified
- the target is local and low-risk
- the change is smaller than a redesign or proposal
- the scope explicitly allows CATALYST or META work

## When NOT to Use
Do not use ADAPT when:
- the issue is architectural or repo-wide
- multiple subsystems must change together
- KeepRoLLMing product code is involved
- the correct solution is still unclear
- the current scope is KR-only and the change is purely CATALYST/meta

In those cases, create a TODO or proposal instead.

## Procedure
1. Read the relevant FEEDBACK or LEARN outcome.
2. Restate the specific problem in one sentence.
3. Identify the smallest file or files involved.
4. Apply the smallest safe change possible.
5. Update `_agent/state/HANDOFF.md` with the adaptation summary.
6. If the lesson is reusable, append a compact note to `_agent/knowledge/MEMORY.md`.
7. Summarize what changed and the expected behavioral improvement.

## Preferred targets
- one `SKILL.md` file
- `AGENTS.md`
- `_agent/knowledge/CONSTRAINTS.md`
- `_agent/knowledge/DONE_CRITERIA.md`
- one workflow doc under `_docs/development/`

## Output format
### Adaptation target
### Problem addressed
### Minimal change applied
### Expected improvement
### Follow-up needed

## Constraints
- Keep changes small, local, and reversible.
- Avoid broad rewrites.
- Do not touch README unless explicitly required by another skill.
- Do not mix CATALYST adaptation with unrelated product work.
