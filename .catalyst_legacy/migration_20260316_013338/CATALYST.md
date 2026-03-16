# CATALYST

CATALYST is the agent-assisted development environment incubated inside KeepRoLLMing.
It is repository-side infrastructure for keeping coding agents aligned on task scope, memory, documentation, and self-improvement.

## Bootstrap architecture

CATALYST uses a three-layer bootstrap model:

1. [QWEN.md](QWEN.md)
   - runner-specific bootstrap entrypoint for Qwen Code
   - should remain short and durable
   - may be regenerated, but must preserve the canonical CATALYST bootstrap block

2. [AGENTS.md](AGENTS.md)
   - canonical workflow specification
   - defines reading order, operating rules, boundaries, and skill usage

3. [README.md](README.md)
   - human/public overview
   - should include a short section that points agent runners toward QWEN and AGENTS

This redundancy is intentional: it keeps CATALYST discoverable even if one bootstrap file drifts.

## Operational loop

CATALYST now uses a layered cognitive loop:
1. think
2. plan (when needed)
3. work
4. feedback
5. learn
6. adapt (when safe)
7. close

This sits on top of the longer-term consolidation loop:
1. capture
2. distill
3. consolidate
4. prune

The runtime state lives under [_agent/state/](_agent/state/), durable knowledge under [_agent/knowledge/](_agent/knowledge/), and executable procedures under [_skills/](_skills/).

## Canonical principles

- Repository docs should guide workflow, not redefine runtime tool schemas.
- `SKILL.md` is canonical inside each skill directory; any `SKILL-<NAME>.md` companion is an alias path/symlink to the same content.
- Self-improvement should favor consolidation over accumulation.
- THINK is the cognitive router; WORK is the default executor; FEEDBACK analyzes recent friction and should recommend a concrete next step; LEARN is the reinforcement entrypoint; ADAPT is for small, safe self-modification only.
- Bootstrap files should be kept synchronized with dedicated maintenance rather than ad-hoc edits.

## Related files

- [QWEN.md](QWEN.md)
- [AGENTS.md](AGENTS.md)
- [README.md](README.md)
- [_agent/knowledge/KNOWLEDGE_BASE.md](_agent/knowledge/KNOWLEDGE_BASE.md)
- [_docs/development/CONSOLIDATION_POLICY.md](_docs/development/CONSOLIDATION_POLICY.md)
- [_skills/SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md](_skills/SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md)


## Core cognitive skills

- [THINK](_skills/THINK/SKILL-THINK.md): clarify objective, scope, and next skill without changing files
- [PLAN](_skills/PLAN/SKILL-PLAN.md): create a bounded execution plan for non-trivial work
- [WORK](_skills/WORK/SKILL-WORK.md): execute the active task through completion
- [FEEDBACK](_skills/FEEDBACK/SKILL-FEEDBACK.md): analyze recent friction and produce an explicit recommended outcome
- [LEARN](_skills/LEARN/SKILL-LEARN.md): distill broader lessons and decide whether adaptation is warranted
- [ADAPT](_skills/ADAPT/SKILL-ADAPT.md): apply a minimal, low-risk workflow improvement to CATALYST itself when scope allows
