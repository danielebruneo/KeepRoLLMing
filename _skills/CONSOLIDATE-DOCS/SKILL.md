---
name: CONSOLIDATE-DOCS
description: Merge durable knowledge into canonical docs and remove duplication between agent notes, docs, and skills.
---


# CONSOLIDATE-DOCS Skill

## Goal
Move stable knowledge into the correct canonical files and reduce duplication.

## Procedure
1. Compare the relevant source notes against canonical destinations.
2. Identify duplicated or near-duplicated content.
3. Keep the strongest version in the canonical file.
4. Compress or remove the weaker copies.
5. Preserve links and clarity.
6. Note the consolidation in [_agent/HANDOFF.md](../../_agent/HANDOFF.md).

## Canonical preference
- Public explanation -> [README.md](../../README.md)
- Stable agent summary -> [_agent/KNOWLEDGE_BASE.md](../../_agent/KNOWLEDGE_BASE.md)
- Durable architecture -> [_docs/architecture/](../../_docs/architecture/)
- Durable workflow -> [_docs/development/](../../_docs/development/)
- Durable decision -> [_docs/decisions/DECISIONS.md](../../_docs/decisions/DECISIONS.md)
- Repeatable procedure -> [_skills/](../../_skills/)

## When to Use
- After LEARN or DISTILL-LEARNINGS
- When the same fact appears in multiple files
- When docs became verbose after many iterative updates

