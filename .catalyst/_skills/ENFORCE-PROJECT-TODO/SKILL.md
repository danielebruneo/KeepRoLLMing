# ENFORCE-PROJECT-TODO

This skill explicitly enforces the use of project's TODO list (`_agent/state/TODOS.md`) over internal task management capabilities.

## Purpose
To ensure that agents working with this CATALYST project follow a consistent workflow using the project's primary task coordination system instead of their built-in internal todo tracking.

## Workflow Rules
1. **Task Source**: All work should originate from `_agent/state/TODOS.md`
2. **Never Use Internal Todos**: Do not rely on Qwen agent's internal todo management for this project
3. **Explicit File Reading**: When starting any new task, explicitly read the content of `_agent/state/TODOS.md` to determine what work is available
4. **Use Project Skills**: Leverage skills like `PICKUP-TASK` and `CLOSE-TASK` that operate on the project's TODO list files

## Key Enforcement Points
- Only process tasks from `_agent/state/TODOS.md`
- When creating a new task, it must be added to this file first (via todo_write)
- Do not create separate internal tracking lists or structures for work items
- The agent should be aware that `_agent/state/TODOS.md` is the sole source of truth

## Usage Guidance
When working with tasks:
1. Use `todo_write` to add new items to `_agent/state/TODOS.md`
2. Use `PICKUP-TASK` to move a task from TODOS.md to ACTIVE_TASK.md
3. Always verify you're using the project's TODO system as your authoritative source

## Implementation Notes
This skill is loaded by CATALYST and enforced during agent operation to ensure adherence to project workflow protocols.