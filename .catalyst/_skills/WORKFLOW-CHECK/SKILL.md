---
name: WORKFLOW-CHECK
description: Validates proper task lifecycle compliance and workflow rules adherence.
---

# WORKFLOW-CHECK Skill

## Goal
Validate that tasks follow proper lifecycle: PICKUP → WORK → CLOSE-TASK, ensuring all workflow rules are followed.

## Purpose
This skill enforces the correct task lifecycle and checks for common workflow violations. It ensures agents follow CATALYST conventions when managing tasks.

## Procedure

### 1. Check Active Task State
- Read `_agent/state/ACTIVE_TASK.md`
- Verify:
  - File exists (not deleted)
  - Has valid structure (metadata header)
  - Status is valid (active, resumed, suspended)
  - Linked to TODO if applicable

### 2. Check Task Lifecycle
For each state file:
- **PICKUP-TASK compliance:**
  - Was PICKUP-TASK used to start task?
  - Is ACTIVE_TASK.md linked to TODO entry?
  
- **CLOSE-TASK compliance:**
  - Was ACTIVE_TASK.md cleared (not deleted) on completion?
  - Is there COMPLETED_TASKS.md entry?
  - Is TODO entry marked as completed with link?
  - Is HANDOFF.md updated?

- **SUSPEND-TASK compliance:**
  - If suspended, is there STALE_TASKS file?
  - Is there HANDOFF entry for suspension?
  - Is ACTIVE_TASK.md cleared (not deleted)?

### 3. Check THINK/PLAN Usage
- Verify THINK was called before substantial work if task was unclear
- Verify PLAN was called before complex/risky work
- Check for patterns of skipping cognitive steps

### 4. Check Documentation Links
- TODO ↔ ACTIVE_TASK links
- TODO ↔ COMPLETED_TASKS links
- STALE_TASKS ↔ HANDOFF links
- SCOPE.md ↔ ACTIVE_TASK alignment

### 5. Generate Report
Create report with:
- Lifecycle compliance status
- Violations found
- Recommendations for fixes

## When to Use
- After completing a task (before CLOSE-TASK)
- Before committing changes
- When using USE-CATALYST
- Periodic workflow health checks
- Suspecting workflow violations

## Common Violations Detected

### Critical Violations
- ACTIVE_TASK.md deleted instead of cleared
- Task started without PICKUP-TASK
- COMPLETED_TASKS entry missing
- Scope violations (tests touching implementation)

### Warnings
- TODO entry not linked to task state
- THINK not used for unclear task
- PLAN skipped for complex task
- HANDOFF entry missing for suspension

### Informational
- Task completion rate
- Average task duration
- Suspension frequency

## Example Report

```
🔍 WORKFLOW COMPLIANCE CHECK
Generated: 23/03/2026 17:30:00

## Overall Status: ✅ COMPLIANT

### Task Lifecycle: COMPLIANT
✅ PICKUP-TASK used to start current task
✅ ACTIVE_TASK.md properly managed (cleared, not deleted)
✅ COMPLETED_TASKS.md has entry for completed tasks
✅ TODO list properly linked

### Cognitive Workflow: ⚠️  1 WARNING
⚠️  THINK not used before substantial work
  - Task: "Add rate limiting"
  - Issue: Task was unclear, should have used THINK first
  - Recommendation: Call THINK next time for unclear tasks

### Documentation Links: ✅ COMPLIANT
✅ TODO → ACTIVE_TASK link: Present
✅ TODO → COMPLETED link: Present
✅ STALE_TASKS → HANDOFF links: All present

### Scope Compliance: ✅ COMPLIANT
✅ All modified files within SCOPE.md boundaries

## Recommendations
- Consider using THINK for unclear tasks
- Continue following proper lifecycle

📊 Full report: _agent/state/WORKFLOW_AUDIT/20260323_173000_workflow_check.md
```

## Related Skills
- **[USE-CATALYST]** - Auto-invokes workflow check
- **[WORKFLOW-AUDIT]** - Comprehensive state review
- **[PICKUP-TASK]** - Starts task lifecycle
- **[CLOSE-TASK]** - Ends task lifecycle
- **[SUSPEND-TASK]** - Pauses task lifecycle
- **[THINK]** - Cognitive workflow step

## Integration with Workflow
```
1. PICKUP-TASK starts task
2. WORK on task
3. WORKFLOW-CHECK validates lifecycle
   - If violations: Fix before continuing
4. CLOSE-TASK completes task
5. WORKFLOW-CHECK validates completion
```

## DateTime Tracking
All checks use timestamp format: `DD/MM/YYYY HH:MM:SS`

## Output Files
- Console: Summary with violations
- `_agent/state/WORKFLOW_AUDIT/{timestamp}_workflow_check.md`: Full report