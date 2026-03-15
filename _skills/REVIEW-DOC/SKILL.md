---
name: REVIEW-DOC
description: Reviews markdown documentation to keep it accurate, non-redundant, and aligned with the current project state.
---

# REVIEW-DOC Skill

## Goal
Review repository markdown files and keep them accurate, relevant, and non-duplicative.

## Procedure
1. Inspect key docs under [_docs/](../../_docs/) and [_agent/](../../_agent/) - examine all relevant documentation.
2. Identify stale, duplicated, or misleading information - find content that's outdated or incorrect.
3. Preserve durable information and remove task-local noise from human-facing docs - keep only essential content.
4. Keep internal operational docs concise and useful - maintain clear separation of concerns.
5. Summarize major documentation fixes in [_agent/HANDOFF.md](../../_agent/HANDOFF.md) - document changes made.

## When to Use
- When reviewing all documentation for accuracy and completeness
- When updating documentation after code changes
- When cleaning up outdated or redundant content

## Examples
- Verifying that README reflects current implementation status (e.g., checking that usage examples match actual API calls)
- Checking that configuration documentation matches actual behavior (e.g., confirming environment variables are properly documented)
- Removing obsolete sections from architecture docs (e.g., deleting outdated module information)

## Documentation Reference Guidance
When reviewing documentation, consider:
- Architecture decisions in DECISIONS.md when understanding design rationale
- Configuration details in CONFIGURATION.md when evaluating parameter changes
- Caching mechanisms in CACHING_MECHANISM.md when assessing performance impact  
- Performance metrics in PERFORMANCE.md when analyzing optimization opportunities