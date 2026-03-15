---
name: WORK
description: Automates the full workflow of checking active tasks, picking up tasks when needed, implementing them through completion, and properly closing completed tasks with commit and handoff updates.
---

# WORK Skill

## Goal
Automate a complete work cycle that checks if an active task is pending, picks up new tasks if necessary, implements the current task until completion, then properly commits changes, closes the task, and updates handoff records.

## When to Use
- When wanting to systematically progress through planned enhancements without manual intervention  
- When seeking automated workflow for task execution and management
- After completing a full learning or feedback cycle where new tasks should be picked up

## Procedure
1. **Check Current Task Status**: Examine `_agent/state/ACTIVE_TASK.md` to determine if there's an active task in progress
2. **Task Decision Logic**: 
   - If active task exists and is still pending, proceed with implementation
   - If no active task or current task is completed, pick up the next available task from TODO list  
3. **Implement Task**: Execute the work described in ACTIVE_TASK.md until completion criteria are met
4. **Commit Changes**: Commit all changes made during task execution using appropriate commit message format 
5. **Close Task**: Update `_agent/state/ACTIVE_TASK.md` to mark task as completed and add final status information  
6. **Update Handoff**: Modify `_agent/state/HANDOFF.md` with completion record, including files touched, lessons learned
7. **Update TODO List**: Remove the completed task from `_agent/state/TODOS.md` 
8. **Generate Summary Report**: Create a brief summary of what was accomplished and why it matters

## Constraints
- Do not make broad refactorings or changes beyond scope defined in active task
- Maintain all existing workflow templates and conventions consistently  
- Ensure commit messages follow established patterns for different types of work (task, learn, scripts)
- Preserve datetime tracking throughout the process (DD/MM/YYYY HH:MM:SS format)

## Key Integration Points
This skill integrates with other CATALYST skills:
- **PICKUP-TASK**: For selecting new tasks when no active task exists
- **LEARN**: When learning opportunities emerge during work execution  
- **FEEDBACK**: For analysis of workflow patterns and potential improvements
- **UPDATE-KNOWLEDGE-BASE**: To maintain updated understanding after completing work

## Template Reference
This skill follows the same pattern as other skills, using:
- `_templates/ACTIVE_TASK.template.md` for task structure when picking up new tasks  
- Standard datetime tracking format: DD/MM/YYYY HH:MM:SS 
- Consistent file naming and organization conventions  

## Workflow Efficiency Focus
The skill is designed to minimize manual intervention while maintaining quality standards:
1. **Automated Decision Making**: Determine whether to continue existing work or pick up new task based on clear criteria
2. **Sequential Execution**: Follow a logical progression through check, decide, implement, commit, close phases  
3. **Consistent Documentation**: Maintain all documentation in standard formats throughout the process
4. **Proper State Management**: Ensure agent state files accurately reflect current workflow progress

## Expected Output 
1. Completed task implementation with proper commits and handoff updates
2. Updated ACTIVE_TASK.md reflecting completion status  
3. Updated HANDOFF.md with final work summary
4. Updated TODO list with completed task removed
5. A brief execution summary for future reference

Note: This skill is meant to be called as a single workflow unit, not broken into multiple steps.