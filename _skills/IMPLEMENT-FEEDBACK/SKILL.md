---
name: IMPLEMENT-FEEDBACK
description: Applies a bounded set of improvements discovered through FEEDBACK when they are concrete, low-risk, and clearly beneficial.
---

# IMPLEMENT-FEEDBACK Skill

## Goal
Turn actionable findings from [FEEDBACK](../FEEDBACK/SKILL-FEEDBACK.md) into small, reviewable repository improvements without broadening scope unnecessarily.

## When to Use
- FEEDBACK identified a concrete fix or workflow improvement
- The change is small, local, and low-risk
- The user explicitly asks to apply the feedback

## Procedure
1. Read the relevant FEEDBACK output and restate the proposed improvement.
2. Identify the smallest set of files involved.
3. Apply the minimal change needed.
4. Update [_agent/HANDOFF.md](../../_agent/HANDOFF.md) and, if needed, [_agent/MEMORY.md](../../_agent/MEMORY.md).
5. If bootstrap files were involved, run [SYNC-BOOTSTRAP-FILES](../SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md).

## Constraints
- Do not turn reflection into a broad refactor.
- If the change is ambiguous or large, write a TODO/proposal instead of implementing it.
