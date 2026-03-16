---
name: CLOSE-TASK
description: Closes or rolls over the active task by updating handoff, memory, and task state.
---

# CLOSE-TASK Skill

## Goal
Close or roll over the active task cleanly with proper documentation of outcomes.

## Procedure
1. Confirm what was completed and what was not - review the actual work done vs planned.
2. Summarize changes in [_agent/state/HANDOFF.md](../../_agent/state/HANDOFF.md) with clear, actionable summary.
3. Add reusable lessons to [_agent/knowledge/MEMORY.md](../../_agent/knowledge/MEMORY.md) when warranted - note insights that could help future work.
4. Clear or replace (do not delete) [_agent/state/ACTIVE_TASK.md](../../_agent/state/ACTIVE_TASK.md) for the next task - prepare for new work.
5. Add completed task summary to [_agent/state/COMPLETED_TASKS.md](../../_agent/state/COMPLETED_TASKS.md) file with:
   - Task title and description
   - Completion date
   - Key outcomes achieved
   - Lessons learned
6. Update [_agent/state/TODOS.md](../../_agent/state/TODOS.md) to mark the completed task as finished

## When to Use
- When completing a task and ready to move on to new work
- After finishing all planned steps in an active task
- When documenting completed work for future reference

## Examples
- Closing documentation review task after verifying all files
- Finalizing code refactoring with proper handoff notes
- Wrapping up feature implementation with summary of changes made

## Template Reference
This skill leverages the structure defined in `_templates/HANDOFF.template.md` which includes:
- Current status field
- What changed section
- What still needs work
- Best next step guidance
- Files touched list
- Risks/warnings
- Suggested commands for verification

## DateTime Tracking
All handoffs created using this skill should include:
- Handoff timestamp in DD/MM/YYYY HH:MM:SS format

## Integration Notes
This skill now automatically integrates with the COMPLETED_TASKS.md file to ensure all completed tasks are properly tracked.