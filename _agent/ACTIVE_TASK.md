# Active Task

## Title
<single concrete task title>

## Goal
<what must be achieved>

## Why this matters
<short context>

## Likely files
- [path/to/file](path/to/file)
- [path/to/other_file](path/to/other_file)

## Constraints
- Preserve external behavior unless the user asked for a change
- Prefer the smallest safe fix
- Avoid unrelated refactors

## Steps
1. Reproduce or inspect the issue
2. Identify the most likely files
3. Apply the smallest sensible change
4. Run targeted validation
5. Update [_agent/HANDOFF.md](HANDOFF.md)

## Done when
- Required behavior is present
- Relevant checks pass
- No unrelated files were changed without reason

## Out of scope
- unrelated cleanups
- broad redesigns
