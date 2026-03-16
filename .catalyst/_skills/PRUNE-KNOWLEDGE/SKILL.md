---
name: PRUNE-KNOWLEDGE
description: Identify stale, duplicated, or overgrown knowledge and trim it without losing important durable information.
---


# PRUNE-KNOWLEDGE Skill

## Goal
Keep CATALYST compact and useful by removing stale, duplicated, or bloated knowledge.

## Procedure
1. Scan `_agent/`, `_docs/`, and `_skills/` for:
   - duplication
   - stale content
   - placeholder text left behind
   - manifesto-like sections that should be procedural
2. Decide whether each item should be:
   - removed
   - merged
   - shortened
   - archived
3. Prefer compression over proliferation.
4. Keep a brief pruning note in [_agent/HANDOFF.md](../../_agent/HANDOFF.md).

## Heuristics
- HANDOFF should not read like a diary.
- KNOWLEDGE_BASE should summarize, not duplicate docs.
- Skills should tell the agent what to do, not deliver essays.
- If two files do the same job, one of them is probably wrong.

## When to Use
- After a consolidation pass
- During periodic LEARN runs
- Before packaging CATALYST for reuse elsewhere

