---
name: SUSPEND-TASK
description: Suspends the current active task by saving it to STALE_TASKS directory and clearing ACTIVE_TASK for new work.
---

# SUSPEND-TASK Skill

## Goal
Suspend the current active task by saving it to `_agent/state/STALE_TASKS/` with a unique timestamped filename, clear the ACTIVE_TASK.md file, and update handoff notes with suspension details.

## Purpose
This skill allows agents to pause work on a current task and switch to a different task without losing progress. The suspended task can be resumed later using the RESUME-TASK skill.

## Procedure

### 1. Read Current State
- Read `_agent/state/ACTIVE_TASK.md` to get current task details
- Read `_agent/state/HANDOFF.md` to understand current context

### 2. Generate Unique Filename
Create filename with timestamp and task key:
```
{timestamp}_{task_key}.md
```
Where:
- `timestamp` = Current datetime in format `20260323_143000`
- `task_key` = Brief key from task title (URL-safe)

### 3. Save to STALE_TASKS
- Copy ACTIVE_TASK.md content to `_agent/state/STALE_TASKS/{timestamp}_{task_key}.md`
- Add metadata header with:
  - Original pickup timestamp
  - Suspension timestamp
  - Suspension reason
  - Estimated completion progress percentage

### 4. Clear ACTIVE_TASK.md
- **Do NOT delete the file** - only clear its content
- Replace with template showing cleared status
- Add note about suspended task location for reference

### 5. Update HANDOFF.md
- Add suspension entry with:
  - Timestamp
  - Reason for suspension
  - Location of suspended task file
  - Current scope if applicable

### 6. Update TODO List (if applicable)
- If the suspended task had a TODO reference, mark it as "suspended"
- Add note about suspension in TODO entry

## When to Use
- When interrupted by higher priority work
- When user explicitly requests to switch tasks
- When current task needs to be paused for external dependencies
- Before using RESUME-TASK to continue later

## Constraints
- Never delete ACTIVE_TASK.md - only clear it
- Always create unique timestamped filename
- Preserve all task metadata in suspended file
- Update HANDOFF.md with suspension details
- Link suspended task back to original TODO if applicable

## Example Usage

### Suspended Task File
```markdown
---
original_pickup: 23/03/2026 10:00:00
suspended_at: 23/03/2026 14:30:00
suspension_reason: User requested switch to higher priority task
estimated_progress: 60%
original_todo_ref: - [ ] Add comprehensive API documentation
---

# Active Task - SUSPENDED

## Status
⏸️ SUSPENDED (not completed)

## Title
Add comprehensive API documentation

## Goal
[Original goal preserved]

## Why this matters
[Original context preserved]

## Clarifications
[Original clarifications preserved]

## Likely Files
[Original files list preserved]

## Constraints
[Original constraints preserved]

## Proposed Approach
[Original approach preserved]

## Test Plan
[Original test plan preserved]

## Done When
[Original done criteria preserved]

## Out of Scope
[Original out of scope preserved]

## Notes for Agent Use
[Original notes preserved]
```

### Active Task After Suspension
```markdown
---
status: CLEARED
cleared_at: 23/03/2026 14:30:00
suspended_task: _agent/state/STALE_TASKS/20260323_143000_add_comprehensive_api_doc.md
---

# Active Task

## Status
📭 CLEARED (suspended task saved to STALE_TASKS)

## Next Task
[Ready for new task pickup via PICKUP-TASK]
```

## Related Skills
- **[RESUME-TASK]** - Restore a suspended task back to ACTIVE_TASK.md
- **[PICKUP-TASK]** - Select new task from TODO list
- **[CLOSE-TASK]** - Complete task (different from suspension)
- **[UPDATE-HANDOFF]** - Update handoff notes
- **[ENFORCE-SCOPE]** - Validate scope before switching tasks

## Integration with Workflow
This skill is part of the task suspension workflow:

```
1. Current task in progress
2. SUSPEND-TASK called
   → Task saved to STALE_TASKS/
   → ACTIVE_TASK.md cleared (not deleted)
   → HANDOFF.md updated
3. New task can be picked up via PICKUP-TASK
4. Later: RESUME-TASK to restore suspended task
```

## DateTime Tracking
All timestamps use format: `DD/MM/YYYY HH:MM:SS`

## Template Reference
Suspended task files preserve the structure from `_templates/ACTIVE_TASK.template.md` with added metadata header.