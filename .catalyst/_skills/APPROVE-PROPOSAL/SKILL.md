---
name: APPROVE-PROPOSAL
description: Reviews and approves a TODO proposal, moving it from TODO_PROPOSALS to the official TODO list.
---

# APPROVE-PROPOSAL Skill

## Goal
Review a pending TODO proposal from `_agent/state/TODO_PROPOSALS/` and move it to the official TODO list in `_agent/state/TODOS.md`.

## Purpose
This skill provides explicit approval mechanism for TODO proposals, ensuring all new tasks are properly reviewed before becoming official project items. It maintains traceability between proposals and approved tasks.

## Procedure

### 1. List Pending Proposals
- Read `_agent/state/TODO_PROPOSALS/` directory
- Display all pending proposals with:
  - Title
  - Priority
  - Estimated effort
  - Related task (if any)
  - Creation date

### 2. Select Proposal to Approve
- Allow user to specify which proposal to approve
- Or select by filename pattern
- Or approve all pending proposals

### 3. Review Proposal Details
- Read proposal file
- Verify:
  - Clear description
  - Defined priority
  - Estimated effort
  - Context linkage
  - Scope compliance

### 4. Create TODO Entry
- Add new entry to `_agent/state/TODOS.md`
- Preserve proposal details:
  - Title and description
  - Priority and effort estimate
  - **Proposal reference:** Link back to original proposal
  - **Approved:** Timestamp and who approved

### 5. Update Proposal Status
- In original proposal file:
  - Add approval timestamp
  - Link to new TODO entry
  - Mark as "Approved"
  - Optionally move to archive or keep for audit trail

### 6. Update HANDOFF.md
- Record approval action
- Note: Which proposal was approved
- Link to new TODO entry

### 7. Optional: Pick Up Task
- If user wants to start immediately:
  - Call PICKUP-TASK for the new task
  - Or leave in TODO for later

## When to Use
- Agent or user wants to approve a TODO proposal
- After reviewing discovered improvements
- Batch approval of multiple proposals
- Before starting work on approved proposal

## Constraints
- Always preserve original proposal for audit trail
- Link new TODO entry back to proposal
- Respect priority and effort estimates
- Validate proposal before approval
- Update HANDOFF.md with approval details

## Example Usage

### Approve Single Proposal
```bash
# Approve by filename
skill: "APPROVE-PROPOSAL" with filename="20260323_153000_add_rate_limiting.md"
```

### Approve All Pending
```bash
skill: "APPROVE-PROPOSAL" with action="approve_all"
```

### TODO Entry After Approval
```markdown
### Features
- [ ] Add rate limiting for API endpoints
  - **Approved:** 23/03/2026 16:00:00
  - **From proposal:** _agent/state/TODO_PROPOSALS/20260323_153000_add_rate_limiting.md
  - **Priority:** High
  - **Effort:** Medium
  - **Context:** Discovered during authentication implementation
```

### Updated Proposal Status
```markdown
---
title: Add rate limiting for API endpoints
created: 23/03/2026 15:30:00
approved: 23/03/2026 16:00:00
approved_by: Agent (via APPROVE-PROPOSAL)
status: Approved → TODO
related_todo: - [ ] Add rate limiting for API endpoints (TODOS.md)
---
```

## Related Skills
- **[PROPOSE-TODO]** - Create new proposal
- **[REJECT-PROPOSAL]** - Discard proposal
- **[PICKUP-TASK]** - Start work on approved task
- **[UPDATE-TODO]** - Modify TODO entry after approval
- **[WORKFLOW-AUDIT]** - Review all proposals and approvals

## Integration with Workflow
```
1. PROPOSE-TODO creates proposal
2. Review: APPROVE-PROPOSAL
   → Proposal approved
   → Entry added to TODOS.md
   → Link preserved
3. PICKUP-TASK to work on it
4. CLOSE-TASK when complete
```

## DateTime Tracking
All timestamps use format: `DD/MM/YYYY HH:MM:SS`

## Template Reference
Approved proposals maintain original structure with added approval metadata.