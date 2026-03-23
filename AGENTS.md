# CATALYST Agent Bootstrap

Canonical CATALYST core:
- [.catalyst/AGENTS.md](.catalyst/AGENTS.md)

Read in this order:
1. [.catalyst/AGENTS.md](.catalyst/AGENTS.md)
2. [_agent/state/SCOPE.md](_agent/state/SCOPE.md)
3. [_agent/state/ACTIVE_TASK.md](_agent/state/ACTIVE_TASK.md)
4. [_agent/state/HANDOFF.md](_agent/state/HANDOFF.md)
5. [_agent/self/MEMORY.md](_agent/self/MEMORY.md)
6. [_project/KNOWLEDGE_BASE.md](_project/KNOWLEDGE_BASE.md) when relevant
7. host project docs under [_docs/](_docs/)

## Workflow Enforcement

When working with tasks, follow these enforcement patterns:

### Task Lifecycle
- **Start**: Use `PICKUP-TASK` to select from TODO list
- **Work**: Execute task, use `ENFORCE-SCOPE` to validate file changes
- **Interrupt**: Use `SUSPEND-TASK` to pause and switch to another task
- **Resume**: Use `RESUME-TASK` to continue suspended work
- **Complete**: Use `CLOSE-TASK` to finalize (preserves ACTIVE_TASK.md)

### Discovery and Proposals
- **New ideas**: Use `PROPOSE-TODO` to capture improvements
- **Approval**: Use `APPROVE-PROPOSAL` to make proposal official
- **Never add directly to TODO** without approval

### Compliance
- **Auto-check**: Mention "catalyst" to auto-invoke `USE-CATALYST`
- **Manual check**: Use `WORKFLOW-CHECK` to validate lifecycle
- **Full audit**: Use `WORKFLOW-AUDIT` for comprehensive review
- **Scope**: Use `UPDATE-SCOPE` to modify boundaries when needed

### Key Files
- `_agent/state/ACTIVE_TASK.md` - Never deleted, only cleared
- `_agent/state/SCOPE.md` - Defines work boundaries
- `_agent/state/STALE_TASKS/` - Suspended tasks (timestamped)
- `_agent/state/TODO_PROPOSALS/` - Pending approvals
- `_agent/state/COMPLETED_TASKS.md` - Archive of finished tasks

### Pattern Learning
- **`_agent/knowledge/ENVIRONMENT.md`** - Environmental quirks, LLM patterns, and tool-calling workarounds
- **Rule:** When your first approach fails and you find a workaround, automatically save the pattern using `CAPTURE-ENVIRONMENT-LESSON`
- **Why:** Prevents repeating the same mistakes in future sessions

Runtime note:
- Skills are markdown procedures.
- `.qwen/skills/` is a generated runtime projection registry.
- Do **not** treat `.qwen/skills/` as the canonical source of skill content.
