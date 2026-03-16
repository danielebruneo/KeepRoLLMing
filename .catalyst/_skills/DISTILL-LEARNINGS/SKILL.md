---
name: DISTILL-LEARNINGS
description: Convert raw feedback, handoff notes, and repeated observations into compact reusable lessons and clear destinations.
---


# DISTILL-LEARNINGS Skill

## Goal
Turn raw observations into stable, compact lessons that are worth keeping.

## Procedure
1. Gather candidate inputs from [_agent/state/HANDOFF.md](../../_agent/state/HANDOFF.md), [_agent/knowledge/MEMORY.md](../../_agent/knowledge/MEMORY.md), [_agent/state/TODOS.md](../../_agent/state/TODOS.md), [_agent/state/COMPLETED_TASKS.md](../../_agent/state/COMPLETED_TASKS.md), and recent FEEDBACK output.
2. Remove one-off noise and session-only chatter.
3. Group repeated patterns.
4. For each surviving lesson, decide its destination:
   - memory
   - knowledge base
   - decision
   - skill
   - TODO/proposal
5. Produce a short distilled list, not a narrative dump.

## Output format
For each lesson:
- Lesson
- Why it matters
- Destination
- Action (update now / queue)

## When to Use
- Before LEARN updates docs or skills
- When HANDOFF/MEMORY are growing noisy
- After several feedback items point to the same issue

