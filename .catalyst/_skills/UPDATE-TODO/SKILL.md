---
name: UPDATE-TODO
description: Updates and maintains the TODO list with new items, status changes, and completion tracking.
---

# UPDATE-TODO Skill

## Goal
Update and maintain the TODO list with new items, status changes, and completion tracking while preserving project standards.

## Procedure
1. Read current _agent/TODOS.md file
2. Allow user to add new tasks or modify existing ones
3. **Track ACTIVE_TASK linkage:**
   - If task is currently active, add reference to ACTIVE_TASK.md
   - Example: `- [ ] Task description → [Active](_agent/state/ACTIVE_TASK.md)`
4. **Validate task structure** follows project standards
5. **Track completion status:**
   - When marking as completed, add link to COMPLETED_TASKS.md
   - Example: `- [x] Task description → [Completed: DD/MM/YYYY](_agent/state/COMPLETED_TASKS.md#task-title)`
6. Maintain consistent formatting and categorization
7. Save updated file

## When to Use
- When adding new ideas or enhancements to the project
- When updating task status (marking as completed, changing priority)
- When reorganizing tasks in the list

## Examples
- Adding "Add support for different summarization modes" to the TODO list
- Marking "Create usage examples for streaming responses" as completed 
- Reordering tasks based on priority changes
- Updating task descriptions with more specific details

## Template Reference
This skill maintains the structure of `_agent/TODOS.md` which includes:
- Project enhancements section with categorized tasks
- Maintenance tasks section
- Long-term goals section
- Consistent checkbox format for task tracking