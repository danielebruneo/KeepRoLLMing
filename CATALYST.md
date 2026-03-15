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

CATALYST follows a repeating loop:
1. capture
2. distill
3. consolidate
4. prune

That loop is reflected in the agent state files under [_agent/state/](_agent/state/) and in the self-improvement skills under [_skills/](_skills/).

## Canonical principles

- Repository docs should guide workflow, not redefine runtime tool schemas.
- `SKILL.md` is canonical inside each skill directory; any `SKILL-<NAME>.md` companion is an alias path/symlink to the same content.
- Self-improvement should favor consolidation over accumulation.
- Bootstrap files should be kept synchronized with dedicated maintenance rather than ad-hoc edits.

## Related files

- [QWEN.md](QWEN.md)
- [AGENTS.md](AGENTS.md)
- [README.md](README.md)
- [_agent/knowledge/KNOWLEDGE_BASE.md](_agent/knowledge/KNOWLEDGE_BASE.md)
- [_docs/development/CONSOLIDATION_POLICY.md](_docs/development/CONSOLIDATION_POLICY.md)
- [_skills/SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md](_skills/SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md)
