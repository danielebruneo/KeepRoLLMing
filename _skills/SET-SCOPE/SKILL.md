# SET-SCOPE

Purpose:
Initialize or explicitly change the current agent scope.

Use when:
- user explicitly requests work on a system (KR, CATALYST, META)
- a new ACTIVE_TASK defines a different domain

Actions:
1. Update `_agent/state/SCOPE.md`
2. Record:
   - Current Scope
   - Source
   - Reason
3. Reset Proposed Next Scopes.
