---
name: SAFE-REFACTOR
description: Refactors code while preserving behavior and keeping scope tightly controlled.
---

# SAFE-REFACTOR Skill

## Goal
Improve internal structure without changing intended behavior to maintain stability.

## Procedure
1. Define the refactor scope clearly - specify exactly what needs to be changed.
2. Identify behavior that must remain unchanged - document what should not be affected.
3. Prefer incremental, reviewable changes - make small, focused modifications.
4. Validate with the most relevant checks - ensure functionality still works as expected.
5. Stop if the work starts turning into a redesign - avoid broad rewrites that change core logic.

## When to Use
- When improving code structure without altering functionality
- When making small, targeted changes to improve readability or performance
- When refactoring for clarity while maintaining exact behavior

## Examples
- Renaming variables for better clarity
- Restructuring code blocks for improved readability
- Simplifying complex conditional logic
- Improving function signatures without changing behavior