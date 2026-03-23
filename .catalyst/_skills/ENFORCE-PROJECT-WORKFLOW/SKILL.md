# ENFORCE-PROJECT-WORKFLOW

This skill explicitly enforces the proper use of CATALYST's project workflow over Qwen's built-in capabilities.

## Purpose
To ensure agents working with this CATALYST project only utilize the persistent, structured task management system and avoid using ephemeral internal todo tracking mechanisms for actual project work items.

## Key Principles
1. **For REAL PROJECT TASKS** (features, enhancements, bug fixes):
   - Do NOT use `todo_write` tool as it creates ephemeral tasks not persisted to project files
   - Instead, directly manage `_agent/state/TODOS.md`
   - Use `PICKUP-TASK` skill for moving items from TODOS.md to ACTIVE_TASK.md
   - Use `CLOSE-TASK` skill for proper completion tracking

2. **For Internal Agent Sub-tasks**:
   - The `todo_write` tool is acceptable for agent's own reasoning steps
   - These are ephemeral and only help with internal planning
   - Not intended for project task persistence

## Workflow Enforcement
When working on tasks, agents must follow these rules:

### Task Creation/Management
- Project tasks should be added directly to `_agent/state/TODOS.md` 
- Never use `todo_write` for creating persistent project work items
- Internal subtasks can be managed with built-in todo systems

### Task Execution
- Always read from and write to the project's structured files:
  - Check `_agent/state/TODOS.md` first for available tasks
  - Use `PICKUP-TASK` skill to move a task to ACTIVE_TASK.md 
  - Complete work using `CLOSE-TASK` when done

## Enforcement Mechanism
This skill is loaded by CATALYST and overrides default behavior in this project context, ensuring agents maintain the proper project-level tracking protocol.