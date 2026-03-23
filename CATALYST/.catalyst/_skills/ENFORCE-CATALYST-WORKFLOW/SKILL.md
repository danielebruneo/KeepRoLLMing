# ENFORCE-CATALYST-WORKFLOW

This skill enforces the proper use of CATALYST's structured workflow for task management in this project.

## Purpose
To explicitly instruct agents to follow the CATALYST framework protocols when managing work items, specifically ensuring that:
1. Real project tasks are tracked through persistent files (`_agent/state/TODOS.md`, `ACTIVE_TASK.md`, `COMPLETED_TASKS.md`)
2. Ephemeral internal todo lists (via `todo_write`) are only used for agent's own sub-tasking
3. All interactions with the task management system follow CATALYST conventions

## Workflow Enforcement Rules
1. **For Persistent Project Tasks**:
   - DO NOT use `todo_write` tool
   - Instead, directly modify `_agent/state/TODOS.md`
   - Use skills: `PICKUP-TASK`, `CLOSE-TASK`

2. **For Agent Internal Sub-tasks**:
   - The `todo_write` tool is acceptable for internal planning steps
   - These are temporary and not saved to project files

## Implementation Guidance
When working on a task:
- Check `_agent/state/TODOS.md` first 
- Use proper CATALYST skills (`PICKUP-TASK`, etc.) rather than built-in todo systems
- Maintain clear separation between agent's internal work planning vs. project-level tracking

This skill is loaded by the agent to ensure consistent adherence to this repository's workflow requirements.