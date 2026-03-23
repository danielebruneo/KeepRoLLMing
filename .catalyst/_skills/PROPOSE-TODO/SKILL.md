---
name: PROPOSE-TODO
description: Captures new task ideas discovered during work and adds them to TODO_PROPOSALS for approval.
---

# PROPOSE-TODO Skill

## Goal
Capture new task ideas or improvements discovered during work, save them to `_agent/state/TODO_PROPOSALS/`, and link to the current ACTIVE_TASK context.

## Purpose
This skill allows agents to record potential improvements or new requirements discovered during implementation without derailing current work. The proposal requires explicit approval before becoming an official TODO item.

## Procedure

### 1. Read Context
- Read `_agent/state/ACTIVE_TASK.md` to understand current work context
- Read `_agent/state/HANDOFF.md` for current state
- Read `_agent/state/SCOPE.md` to check scope boundaries

### 2. Capture Proposal
Create proposal with:
- **Title:** Brief description of the improvement/task
- **Description:** Detailed explanation of what should be done
- **Rationale:** Why this is needed (linked to current work)
- **Priority:** Low/Medium/High (agent suggestion)
- **Estimated Effort:** Small/Medium/Large
- **Context:** Link to current ACTIVE_TASK.md
- **Discovered:** Timestamp when proposal was created

### 3. Save to TODO_PROPOSALS
- Create file: `_agent/state/TODO_PROPOSALS/{timestamp}_{proposal_key}.md`
- Include all proposal details
- Add metadata for tracking

### 4. Link to Current Task
- Add reference in ACTIVE_TASK.md under "Discovered Improvements" or "Related TODOs"
- Note: This is a proposal pending approval

### 5. Update HANDOFF.md
- Add note about new proposal
- Include proposal filename for reference

### 6. Optional: Notify User
- Present proposal to user for quick review
- Suggest next steps: APPROVE-PROPOSAL or defer

## When to Use
- During work, discover a bug fix needed
- Implementing feature X, realize API documentation needs updating
- Testing reveals need for better error handling
- Code review suggests refactoring opportunity
- User mentions new requirement during work

## Constraints
- Always link proposal to current ACTIVE_TASK context
- Never add directly to TODO list (requires APPROVE-PROPOSAL)
- Preserve original context of why proposal was created
- Include estimated effort and priority
- Respect current SCOPE (flag if outside scope)

## Example Usage

### Proposal File
```markdown
---
title: Add rate limiting for API endpoints
created: 23/03/2026 15:30:00
priority: High
estimated_effort: Medium
related_task: _agent/state/ACTIVE_TASK.md#add-authentication
proposed_by: Agent (discovered during implementation)
scope_status: Within current scope
---

# TODO Proposal: Add rate limiting for API endpoints

## Description
Implement rate limiting on /v1/chat/completions endpoint to prevent abuse.

## Why This Is Needed
While implementing authentication, discovered no rate limiting exists.
This could lead to:
- API abuse and excessive backend costs
- Denial of service for legitimate users
- Unfair resource allocation

## Context
Discovered during: Add authentication middleware
Current task: _agent/state/ACTIVE_TASK.md

## Suggested Approach
1. Add rate limiting middleware (e.g., slowapi or custom implementation)
2. Configure rate limits per endpoint
3. Add rate limit headers to responses
4. Update API documentation
5. Add tests for rate limit enforcement

## Related Files
- `keeprollming/app.py` - Add middleware
- `keeprollming/config.py` - Add rate limit config
- `tests/test_rate_limiting.py` - New test file

## Notes
- Should be approved before continuing with current task
- Can be deferred if outside current sprint
```

### Handoff Note
```markdown
### New Proposal: rate-limiting
- **File:** `_agent/state/TODO_PROPOSALS/20260323_153000_add_rate_limiting.md`
- **Suggested action:** APPROVE-PROPOSAL or defer to TODO list
```

## Related Skills
- **[APPROVE-PROPOSAL]** - Move proposal to official TODO list
- **[REJECT-PROPOSAL]** - Discard proposal (creates record)
- **[PICKUP-TASK]** - Start work on approved proposal
- **[CLOSE-TASK]** - Complete task, may generate new proposals
- **[UPDATE-SCOPE]** - Update scope if proposal extends boundaries

## Integration with Workflow
```
1. Working on ACTIVE_TASK
2. Discover improvement/issue
3. PROPOSE-TODO
   → Saved to TODO_PROPOSALS/
   → Linked to current task
4. Options:
   a) Approve: APPROVE-PROPOSAL → Official TODO
   b) Defer: Leave in proposals for later
   c) Reject: Delete proposal
5. If approved: PICKUP-TASK to work on it
```

## DateTime Tracking
All timestamps use format: `DD/MM/YYYY HH:MM:SS`

## Template Reference
Proposal files follow the structure defined above with metadata header.