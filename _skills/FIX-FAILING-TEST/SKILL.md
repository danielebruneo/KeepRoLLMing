---
name: FIX-FAILING-TEST
description: Restores intended behavior for a failing test using the smallest safe code or test change.
---

# FIX-FAILING-TEST Skill

## Goal
Fix a failing test safely and with minimal scope.

## Procedure
1. Run or inspect the failing test.
2. Read the assertion and failure output carefully.
3. Decide whether code or test expectations are wrong.
4. Change the smallest relevant area.
5. Re-run targeted validation.
6. Update [_agent/HANDOFF.md](../../_agent/HANDOFF.md).
