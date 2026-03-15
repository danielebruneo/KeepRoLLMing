---
name: THINK
description: Pause execution to clarify objective, scope, and the most appropriate next skill without modifying files.
---

# THINK Skill

## Goal
Act as the cognitive router for CATALYST. Use this skill to analyze the current situation, determine the real objective, check scope, and recommend the next step before any significant action is taken.

## Core rule
THINK does not modify files. It produces reasoning and a recommended next action only.

## When to Use
- When the user request is ambiguous or underspecified
- When multiple skills could apply
- Before starting a new task
- After an error or correction from the user
- Before invoking LEARN or FEEDBACK when the correct next step is unclear

## Procedure
1. Restate the current situation in one short paragraph.
2. Read `_agent/state/SCOPE.md` and identify the active work domain.
3. Check whether there is an active task in `_agent/state/ACTIVE_TASK.md`.
4. Classify the needed action as one of:
   - execution
   - debugging
   - documentation
   - reflection
   - learning
   - adaptation
5. Decide whether a plan is needed before acting.
6. Recommend exactly one next step unless a brief ordered sequence is clearly necessary.

## Output format
### Analysis
Short reasoning about the situation.

### Scope
Current scope from `_agent/state/SCOPE.md`.

### Decision
One of:
- invoke WORK
- invoke PLAN
- invoke FEEDBACK
- invoke LEARN
- invoke ADAPT
- invoke another specific skill
- no action needed

### Next step
Name the recommended skill and why.

## Constraints
- Do not modify files.
- Do not silently expand scope.
- Prefer one clear recommendation over many vague options.
- If scope and requested action conflict, call out the conflict explicitly.
