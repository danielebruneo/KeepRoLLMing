# TODO Workflow Improvement Proposal

## Background

The project now has a `_agent/TODOS.md` file that tracks future enhancements and tasks. To complement this, we should implement skills to manage this workflow effectively.

## Proposed New Skills

### 1. COMPLETED-TASKS-SKILL
**Goal**: Maintain a record of completed tasks for historical tracking and project metrics

**Procedure**:
1. Review current HANDOFF.md to identify completed work
2. Extract key information about the completed task 
3. Add summary to _agent/COMPLETED_TASKS.md file with:
   - Task title and description
   - Completion date
   - Key outcomes achieved
   - Lessons learned
4. Update status in HANDOFF.md to mark as completed

**When to Use**:
- After completing any task that should be tracked for historical purposes
- When closing tasks that have significant impact or learning value

### 2. PICKUP-TASK-SKILL
**Goal**: Automate selection and conversion of TODO items into active tasks

**Procedure**:
1. Read current _agent/TODOS.md file to identify available tasks
2. Allow user to select a specific task from the list (or pick automatically)
3. Convert selected task into ACTIVE_TASK.md using standard format
4. Remove the task from the TODO list after pickup
5. Update HANDOFF.md with initial status

**When to Use**:
- When ready to begin work on a planned enhancement
- When wanting to systematically progress through the TODO list
- When need to select a specific task for immediate attention

### 3. UPDATE-TODO-SKILL  
**Goal**: Update and maintain the TODO list with new items, status changes, and completion tracking

**Procedure**:
1. Read current _agent/TODOS.md file
2. Allow user to add new tasks or modify existing ones
3. Validate task structure follows project standards
4. Maintain consistent formatting and categorization
5. Save updated file

**When to Use**:
- When adding new ideas or enhancements to the project
- When updating task status (marking as completed, changing priority)
- When reorganizing tasks in the list

## Benefits of Implementation

1. **Structured Workflow**: Creates a complete lifecycle for task management from planning to completion
2. **Historical Tracking**: Maintains record of completed work for future reference and metrics
3. **Automation**: Enables systematic progression through planned enhancements
4. **Consistency**: Maintains the same documentation patterns as existing skills
5. **Project Visibility**: Provides clear view of both current work and future plans

## Implementation Notes

- These skills should follow the established pattern of existing skills
- They should integrate with the existing template system for consistency
- They should leverage the existing file structure in _agent/
- They should maintain backward compatibility with current workflows