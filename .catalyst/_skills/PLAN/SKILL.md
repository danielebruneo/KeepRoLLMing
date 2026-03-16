---
name: PLAN
description: Create a bounded execution plan for a non-trivial or underspecified task before implementation.
---

# PLAN Skill

## Goal
Produce a short, concrete, execution-ready plan for the current task so that WORK or another implementation skill can proceed safely.

## When to Use
- When THINK determines the task is non-trivial
- When the active task lacks enough implementation detail
- Before refactors or multi-file changes
- When the user asks for a plan explicitly

## Procedure
1. Read `_agent/state/SCOPE.md` and `_agent/state/ACTIVE_TASK.md`.
2. Restate the goal in one or two sentences.
3. Identify the smallest set of files likely involved.
4. Break the task into a short ordered list of concrete steps.
5. Note validation steps or tests.
6. Identify any risks or boundaries that must not be crossed.
7. Recommend handing execution to WORK or another specific skill.

## Output format
### Goal
### Likely files
### Plan steps
### Validation
### Risks / boundaries
### Recommended next skill

## Constraints
- Keep plans short and actionable.
- Prefer 3-7 steps.
- Do not implement changes directly.
- Do not broaden scope beyond the active task.
