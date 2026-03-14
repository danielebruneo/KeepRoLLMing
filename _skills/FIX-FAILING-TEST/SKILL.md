---
name: FIX-FAILING-TEST
description: Restores intended behavior for a failing test using the smallest safe code or test change.
---

# FIX-FAILING-TEST Skill

## Goal
Fix a failing test safely and with minimal scope to maintain project stability.

## Procedure
1. Run or inspect the failing test - understand what exactly is failing.
2. Read the assertion and failure output carefully - identify the root cause.
3. Decide whether code or test expectations are wrong - determine what should be changed.
4. Change the smallest relevant area - avoid broad modifications that could introduce new issues.
5. Re-run targeted validation - verify fix works without breaking other tests.
6. Update [_agent/HANDOFF.md](../../_agent/HANDOFF.md) with clear summary of fix applied.

## When to Use
- When a test is failing and needs fixing
- When code changes are needed to make tests pass
- When test expectations need adjustment to match actual behavior

## Examples
- Fixing parameter passing issues in test functions (e.g., updating mock calls to match expected parameters)
- Correcting model resolution logic that causes test failures (e.g., adjusting profile resolution in config.py)
- Updating test assertions to match current implementation behavior (e.g., changing expected output format in test cases)