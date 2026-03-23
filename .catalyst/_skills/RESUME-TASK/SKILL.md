---
name: RESUME-TASK
description: Restores a suspended task from STALE_TASKS directory back to ACTIVE_TASK for continued work.
---

# RESUME-TASK Skill

## Goal
Restore a suspended task from `_agent/state/STALE_TASKS/` back to `_agent/state/ACTIVE_TASK.md` for continued work.

## Purpose
This skill allows agents to continue work on a previously suspended task by restoring its state, preserving all original context and metadata.

## Procedure

### 1. List Available Suspended Tasks
- Read `_agent/state/STALE_TASKS/` directory
- Display list of suspended tasks with:
  - Filename (timestamp + task key)
  - Task title (from metadata)
  - Suspension timestamp
  - Estimated progress

### 2. Select Task to Resume
- Allow user to specify which task to resume by:
  - Filename pattern (e.g., "add_comprehensive_api")
  - Task key from title
  - Or select most recent suspension
- Validate selected task exists

### 3. Load Suspended Task
- Read the suspended task file
- Parse metadata header for:
  - Original pickup timestamp
  - Suspension timestamp
  - Suspension reason
  - Estimated progress
  - Original TODO reference

### 4. Update Task Status
- Change status from "SUSPENDED" to "RESUMED"
- Add resume timestamp to metadata
- Update progress if known

### 5. Write to ACTIVE_TASK.md
- Replace ACTIVE_TASK.md content with restored task
- Add "RESUMED" banner at top
- Include original suspension details for context

### 6. Update HANDOFF.md
- Add resume entry with:
  - Timestamp
  - Task that was resumed
  - Original suspension reason (for context)
  - Current scope if applicable

### 7. Optional: Remove from STALE_TASKS
- If task is fully restored, remove from STALE_TASKS/
- OR keep as backup until task completes

## When to Use
- When ready to continue suspended work
- After completing the higher-priority task that caused suspension
- When external dependencies for suspended task are now available
- User explicitly requests to resume a suspended task

## Constraints
- Never modify original suspended task file (create restored copy instead)
- Preserve all original metadata and context
- Add "RESUMED" status indicator
- Update HANDOFF.md with resume details
- Validate task exists before attempting to resume

## Example Usage

### Resume Most Recent Task
```bash
# Skill will list available tasks and select most recent
skill: "RESUME-TASK"
# Or specify task key
skill: "RESUME-TASK" with task_key="add_comprehensive_api"
```

### RESUMED Task Format
```markdown
---
original_pickup: 23/03/2026 10:00:00
suspended_at: 23/03/2026 14:30:00
resumed_at: 23/03/2026 16:45:00
suspension_reason: User requested switch to higher priority task
estimated_progress: 60%
---

# Active Task - RESUMED

## Status
▶️ RESUMED (was suspended, now active)

## Title
Add comprehensive API documentation

## Goal
[Original goal]

## Why this matters
[Original context]

... rest of task preserved ...

## Resumption Notes
- **Was suspended:** 23/03/2026 14:30:00
- **Reason:** User requested switch to higher priority task
- **Progress at suspension:** ~60%
- **Resumed:** 23/03/2026 16:45:00
```

## Related Skills
- **[SUSPEND-TASK]** - Pause current task and save to STALE_TASKS
- **[PICKUP-TASK]** - Start new task from TODO list
- **[CLOSE-TASK]** - Complete task when finished
- **[WORKFLOW-AUDIT]** - Review all suspended tasks
- **[UPDATE-SCOPE]** - Update scope if resuming changes boundaries

## Integration with Workflow
This skill completes the suspension/resumption cycle:

```
1. Task suspended: SUSPEND-TASK
   → Saved to STALE_TASKS/
2. Work on other task
3. Resume: RESUME-TASK
   → Restored to ACTIVE_TASK.md
   → Status: RESUMED
4. Continue work
5. Complete: CLOSE-TASK
   → Moved to COMPLETED_TASKS.md
```

## DateTime Tracking
All timestamps use format: `DD/MM/YYYY HH:MM:SS`

## Template Reference
Restored tasks follow the structure from `_templates/ACTIVE_TASK.template.md` with added "RESUMED" status and metadata.