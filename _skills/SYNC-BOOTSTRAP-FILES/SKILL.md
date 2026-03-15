---
name: SYNC-BOOTSTRAP-FILES
description: Keeps QWEN.md, AGENTS.md, and README.md aligned so the CATALYST bootstrap survives regeneration and documentation drift.
---

# SYNC-BOOTSTRAP-FILES Skill

## Goal
Keep the bootstrap files aligned so runner-specific entrypoints, canonical workflow rules, and human-facing documentation all point to the same CATALYST logic.

## When to Use
- After QWEN regenerates or heavily rewrites `QWEN.md`
- After major workflow changes in `AGENTS.md`
- After README restructuring that could remove agent-discovery cues
- During periodic maintenance or self-improvement cycles

## Canonical roles
- [QWEN.md](../../QWEN.md): thin runner-specific bootstrap loader
- [AGENTS.md](../../AGENTS.md): canonical workflow and operating rules
- [README.md](../../README.md): human/public overview with a short agent-assisted development section

## Procedure
1. Read [QWEN.md](../../QWEN.md), [AGENTS.md](../../AGENTS.md), and [README.md](../../README.md).
2. Verify that `QWEN.md` contains and preserves the canonical `CATALYST-BOOTSTRAP` block.
3. Verify that `AGENTS.md` still describes the bootstrap model and reading order correctly.
4. Verify that `README.md` still contains a short agent-assisted development section that points to QWEN and AGENTS.
5. If drift exists, update the smallest set of files needed to restore alignment.
6. Record any non-obvious lesson in [_agent/MEMORY.md](../../_agent/MEMORY.md).
7. If the bootstrap model itself changed, update [_agent/KNOWLEDGE_BASE.md](../../_agent/KNOWLEDGE_BASE.md) and relevant docs.

## Constraints
- Keep `QWEN.md` short; do not move the full workflow into it.
- Do not remove the bootstrap block markers from `QWEN.md`.
- Do not let `README.md` become the only bootstrap source.
- Preserve human readability in `README.md`; keep the agent section concise.

## Output
Produce a short summary of:
- what drift was found
- what files were synchronized
- whether any follow-up documentation updates are needed
