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
1. **Check Runtime State**: Read `_agent/state/SCOPE.md`, `_agent/state/ACTIVE_TASK.md`, and `_agent/state/HANDOFF.md`.
2. **Task Decision Logic**:
   - If the request or next step is unclear, call [THINK](../THINK/SKILL-THINK.md) first
   - If the active task is complex or under-specified, call [PLAN](../PLAN/SKILL-PLAN.md) before implementation
   - If an active task exists and is still pending, proceed with implementation
   - If no active task exists or the current task is completed, pick up the next available task from TODO list
3. **Implement Task**: Execute the work described in ACTIVE_TASK.md until completion criteria are met
4. **Validate**: Check done criteria and run targeted validation or tests when appropriate
5. **Commit Changes**: Commit changes only when the work is coherent and scoped appropriately
6. **Close Task**: Update `_agent/state/ACTIVE_TASK.md` to mark task as completed and add final status information
7. **Update Handoff**: Modify `_agent/state/HANDOFF.md` with completion record, including files touched and lessons learned
8. **Update TODO List**: Remove or mark the completed task in `_agent/state/TODOS.md` when applicable
9. **Generate Summary Report**: Create a brief summary of what was accomplished and why it matters

## Constraints
- Do not make broad refactorings or changes beyond scope defined in the active task and `_agent/state/SCOPE.md`
- Do not silently expand scope from KR to CATALYST or META work
- Maintain all existing workflow templates and conventions consistently
- Ensure commit messages follow established patterns for different types of work (task, learn, scripts)
- Preserve datetime tracking throughout the process (DD/MM/YYYY HH:MM:SS format)

## Key Integration Points
This skill integrates with other CATALYST skills:
- **PICKUP-TASK**: For selecting new tasks when no active task exists
- **LEARN**: When learning opportunities emerge during work execution  
- **FEEDBACK**: For analysis of workflow patterns and potential improvements
- **ADAPT**: For small, scope-appropriate fixes to CATALYST workflow artifacts discovered during work
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

## Relationship with THINK and PLAN
- THINK is the preferred router when the next step is unclear.
- PLAN should be used before WORK when the task needs decomposition or carries meaningful risk.
- WORK is the default executor after routing and planning are clear.
