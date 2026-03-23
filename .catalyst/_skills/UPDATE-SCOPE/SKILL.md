---
name: UPDATE-SCOPE
description: Updates the current work scope in SCOPE.md to reflect new boundaries or task focus.
---

# UPDATE-SCOPE Skill

## Goal
Update the current work scope definition in `_agent/state/SCOPE.md` to reflect new boundaries, focus areas, or task requirements.

## Purpose
This skill allows agents to formally update their work scope when transitioning between tasks, expanding boundaries, or refocusing efforts. It maintains clear documentation of what is and isn't in scope for current work.

## Procedure

### 1. Read Current Scope
- Read `_agent/state/SCOPE.md` to understand current boundaries
- Note what needs to change

### 2. Define New Scope
Create updated scope with:
- **Current Scope:** Brief description of focus
- **Status:** active/inactive
- **Source:** What triggered this scope (PICKUP-TASK, user request, etc.)
- **Allowed Files:** File patterns that can be modified
- **Excluded Files:** File patterns that should not be modified
- **Why:** Explanation of scope boundaries
- **Related Task:** Link to ACTIVE_TASK.md or COMPLETED_TASKS.md

### 3. Update SCOPE.md
- Replace content with new scope definition
- Add timestamp of update
- Preserve history if needed (optional)

### 4. Validate Against Active Task
- Ensure scope aligns with current ACTIVE_TASK.md
- If misaligned, suggest:
  - Update scope to match task
  - Or change task via PICKUP-TASK

### 5. Update HANDOFF.md
- Record scope update with:
  - Timestamp
  - Previous scope (summary)
  - New scope (summary)
  - Reason for change

### 6. Optional: ENFORCE-SCOPE
- Run validation to ensure no pending violations
- Check all recently modified files against new scope

## When to Use
- Starting a new task with different scope
- User explicitly requests scope change
- Discovered work requires broader/narrower focus
- Transitioning between phases of a task
- Before using ENFORCE-SCOPE for validation

## Constraints
- Always explain "Why" for scope boundaries
- Link to related task (ACTIVE_TASK.md or COMPLETED_TASKS.md)
- Keep scope specific and actionable
- Avoid overly broad scopes
- Update HANDOFF.md with scope change

## Example Usage

### Updating Scope for Testing Task
```bash
skill: "UPDATE-SCOPE"
  current_scope: "Add comprehensive test coverage"
  allowed_files: "tests/, keeprollming/test_*.py"
  excluded_files: "keeprollming/*.py (implementation), config.yaml"
  reason: "Focus on test coverage without affecting implementation"
  related_task: _agent/state/ACTIVE_TASK.md
```

### New SCOPE.md Format
```markdown
# Agent Scope

## Current Scope
Add comprehensive test coverage for routing module

## Status
active

## Source
PICKUP-TASK (23/03/2026 10:00:00)

## Allowed Files
- tests/test_routing.py
- tests/test_config.py
- _agent/state/

## Excluded Files
- keeprollming/routing.py (implementation)
- keeprollming/config.py (implementation)
- config.yaml
- requirements.txt

## Why
This task focuses exclusively on test coverage. Implementation changes
should be in a separate task to maintain clear separation of concerns.

## Related Task
_active_task: _agent/state/ACTIVE_TASK.md

## Last Updated
23/03/2026 10:00:00
```

## Related Skills
- **[ENFORCE-SCOPE]** - Validate changes against scope
- **[PICKUP-TASK]** - Sets initial scope for new task
- **[CLOSE-TASK]** - Records final scope in handoff
- **[USE-CATALYST]** - Auto-validates scope adherence

## Integration with Workflow
```
1. PICKUP-TASK → Sets initial scope
2. Work on task
3. Need scope change → UPDATE-SCOPE
4. ENFORCE-SCOPE validates changes
5. CLOSE-TASK → Records scope adherence
```

## DateTime Tracking
All timestamps use format: `DD/MM/YYYY HH:MM:SS`

## Template Reference
Scope definitions follow the structure defined in `_agent/state/SCOPE.md`.