# Development Workflow

This file is for human contributors.

## Suggested workflow
1. Check `_agent/state/SCOPE.md`
2. Use [THINK](../../_skills/THINK/SKILL-THINK.md) if the next action is unclear
3. Create or refresh `_agent/state/ACTIVE_TASK.md`
4. Use [PLAN](../../_skills/PLAN/SKILL-PLAN.md) when the task is non-trivial or under-specified
5. Inspect relevant code, docs, and tests
6. Implement the smallest viable change, usually through [WORK](../../_skills/WORK/SKILL-WORK.md)
7. Run targeted tests or validation
8. Run broader checks if needed
9. Update `_agent/state/HANDOFF.md`
10. Use [FEEDBACK](../../_skills/FEEDBACK/SKILL-FEEDBACK.md) and [LEARN](../../_skills/LEARN/SKILL-LEARN.md) when recent work reveals process improvements
11. Use [ADAPT](../../_skills/ADAPT/SKILL-ADAPT.md) only for small, local, low-risk CATALYST improvements
12. Update docs only when behavior or architecture changed

## Principles
- Small, explicit tasks
- Minimal patches
- Tests before broad cleanup
- Decisions documented when durable

## Template Compliance
All documentation and task files should:
1. Follow the [ACTIVE_TASK.template.md](../_templates/ACTIVE_TASK.template.md) format
2. Include cross-references to related components
3. Maintain consistent structure throughout project


## Cognitive routing
- THINK first when multiple skills could apply.
- PLAN before WORK when the task needs decomposition.
- LEARN should end in an explicit outcome: no action, proposal/TODO, or ADAPT recommendation.
- ADAPT must not interrupt active KeepRoLLMing product work unless the scope explicitly includes CATALYST or META.
