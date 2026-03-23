---
name: WORKFLOW-AUDIT
description: Reviews all agent state files for consistency, detects issues, and generates compliance report.
---

# WORKFLOW-AUDIT Skill

## Goal
Comprehensive review of all agent state files to detect inconsistencies, orphaned tasks, and workflow violations. Generates a detailed compliance report.

## Purpose
This skill provides a diagnostic view of the entire workflow state, helping identify issues like:
- Orphaned ACTIVE_TASK (no TODO reference)
- Stale tasks in STALE_TASKS/ without HANDOFF entry
- TODO items without corresponding ACTIVE_TASK or COMPLETED entry
- Scope violations
- Incomplete handoffs
- Broken links between state files

## Procedure

### 1. Scan All State Files
Read and analyze:
- `_agent/state/ACTIVE_TASK.md` - Check validity, TODO link
- `_agent/state/COMPLETED_TASKS.md` - Verify completeness
- `_agent/state/HANDOFF.md` - Check for orphaned entries
- `_agent/state/SCOPE.md` - Validate format and source
- `_agent/state/TODOS.md` - Check for broken links
- `_agent/state/TODO_PROPOSALS/` - List all pending proposals
- `_agent/state/STALE_TASKS/` - List all suspended tasks

### 2. Check Task Lifecycle
For each task:
- **Active:** Is ACTIVE_TASK linked to a TODO or proposal?
- **Suspended:** Is there a HANDOFF entry for suspension?
- **Completed:** Is there a COMPLETED_TASKS entry with link back to TODO?

### 3. Validate Link Integrity
- TODO → ACTIVE_TASK (if active)
- TODO → COMPLETED_TASKS (if completed)
- ACTIVE_TASK → HANDOFF (suspension/resumption)
- STALE_TASKS → HANDOFF (suspension entry)

### 4. Check Scope Compliance
- Verify ACTIVE_TASK aligns with SCOPE.md
- Check recently modified files (if tracked)
- Flag scope violations

### 5. Generate Report
Create report in `_agent/state/WORKFLOW_AUDIT/{timestamp}_audit.md` with:
- Summary of state
- Issues detected
- Recommendations
- Links to relevant files

### 6. Display Report
Show audit results to user with:
- Overall health status
- Critical issues (must fix)
- Warnings (should fix)
- Informational notes

## When to Use
- Periodic workflow health check
- Before committing major changes
- When suspecting state inconsistencies
- After multiple task suspensions/resumptions
- When using USE-CATALYST

## Issues Detected

### Critical Issues
- ACTIVE_TASK.md is empty or corrupted
- HANDOFF.md has entries without corresponding state files
- SCOPE.md references non-existent files
- TODO list has broken links

### Warnings
- Stale task without HANDOFF suspension entry
- TODO item not linked to any task state
- Scope mismatch with active task
- Proposal older than 7 days (stale proposal)

### Informational
- Number of suspended tasks
- Number of pending proposals
- Task completion rate

## Example Report

```
🔍 WORKFLOW AUDIT REPORT
Generated: 23/03/2026 17:00:00

## Overall Status: ⚠️ WARNINGS

### Summary
- Active tasks: 1
- Suspended tasks: 2
- Completed tasks: 5
- Pending proposals: 3

### Critical Issues: 0
✅ No critical issues found

### Warnings: 2
⚠️  Stale task without HANDOFF entry:
    - File: _agent/state/STALE_TASKS/20260322_100000_old_feature.md
    - Recommendation: Add HANDOFF suspension entry or resume/delete

⚠️  Proposal older than 7 days:
    - File: _agent/state/TODO_PROPOSALS/20260315_140000_refactor.md
    - Recommendation: Approve or reject stale proposal

### Recommendations
1. Add HANDOFF entry for suspended task
2. Review stale proposals and approve/reject
3. Consider cleaning up old suspended tasks

### State File Integrity
- ACTIVE_TASK.md: ✅ Valid
- COMPLETED_TASKS.md: ✅ Valid
- HANDOFF.md: ⚠️ 2 warnings
- SCOPE.md: ✅ Valid
- TODOS.md: ✅ Valid

### Link Validation
- TODO → ACTIVE_TASK: ✅ 1 linked
- TODO → COMPLETED: ✅ 5 linked
- STALE → HANDOFF: ⚠️ 1 missing

## Full Report
See: _agent/state/WORKFLOW_AUDIT/20260323_170000_audit.md
```

## Related Skills
- **[USE-CATALYST]** - Auto-invokes audit on "catalyst" mention
- **[ENFORCE-SCOPE]** - Scope compliance check
- **[WORKFLOW-CHECK]** - Lifecycle compliance check
- **[CLOSE-TASK]** - Ensures proper completion
- **[SUSPEND-TASK]** - Creates proper suspension entries

## DateTime Tracking
Audit reports use timestamp format: `YYYYMMDD_HHMMSS_audit.md`

## Output Files
- `_agent/state/WORKFLOW_AUDIT/{timestamp}_audit.md` - Full report
- Console output - Summary with recommendations