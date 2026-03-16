# Consolidation Policy

## Purpose
Prevent CATALYST from drifting into verbose, duplicated, or self-referential documentation.

## The 4-step loop
1. **Capture**
   - Record fresh observations in `_agent/HANDOFF.md`, `_agent/MEMORY.md`, `_agent/TODOS.md`, or a task-local note.
   - Keep capture rough and local.
2. **Distill**
   - Convert raw notes into reusable lessons, proposed actions, or stable conclusions.
   - Use `FEEDBACK` for recent interaction analysis and `DISTILL-LEARNINGS` for broader synthesis.
3. **Consolidate**
   - Move durable information into the right canonical file.
   - Prefer updating one canonical file instead of duplicating the same idea in multiple places.
4. **Prune**
   - Remove stale, superseded, or duplicated content.
   - Archive only when history matters; otherwise compress.

## Canonical destinations
- Session continuation -> `_agent/HANDOFF.md`
- Raw but reusable lesson -> `_agent/MEMORY.md`
- Stable project summary -> `_agent/KNOWLEDGE_BASE.md`
- Durable architecture description -> `_docs/architecture/*`
- Durable development workflow -> `_docs/development/*`
- Durable decision -> `_docs/decisions/DECISIONS.md`
- Repeatable procedure -> `_skills/*`
- Public-facing explanation -> `README.md`

## Consolidation triggers
Run a consolidation step when one of these is true:
- the same fact appears in 2+ files
- HANDOFF starts reading like a diary
- KNOWLEDGE_BASE becomes exhaustive instead of summarizing
- a skill becomes more reflective than procedural
- recent feedback produced stable lessons worth keeping
- several TODOs or completed tasks point to the same structural issue

## Implementation policy
Low-risk improvements may be implemented immediately when clearly safe.
Higher-risk improvements should become TODOs or proposals first.
Prefer small documented changes over large speculative rewrites.
