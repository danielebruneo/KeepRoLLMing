---
name: PICKUP-TASK
description: Selects and converts a task from the TODO list into an active task for execution.
---

# PICKUP-TASK Skill

## Goal
Automate selection and conversion of TODO items into active tasks to enable systematic progression through planned enhancements.

## Procedure
1. Read current _agent/TODOS.md file to identify available tasks
2. Allow user to select a specific task from the list (or pick automatically the one which make more sense as next)
3. **Validate against current SCOPE.md:**
   - Check if task aligns with current scope boundaries
   - If misaligned, suggest UPDATE-SCOPE or PICKUP-TASK for different task
4. Convert selected task into ACTIVE_TASK.md using standard format
5. **Link to TODO:** Add reference in ACTIVE_TASK.md back to original TODO item
6. Remove the task from the TODO list after pickup (or mark as active)
7. Update HANDOFF.md with initial status and scope

## When to Use
- When ready to begin work on a planned enhancement
- When wanting to systematically progress through the TODO list
- When need to select a specific task for immediate attention

## Examples
- Selecting "Add comprehensive API documentation" from TODO list and converting it to active task
- Automatically picking the highest priority task from the list for immediate work
- Explicitly choosing "Implement support for custom summary prompts" as the next task to work on

## Template Reference
This skill uses the structure defined in `_templates/ACTIVE_TASK.template.md` which includes:
- Status field
- Title and user request sections
- Goal and why this matters explanations
- Clarifications section
- Likely files list
- Constraints 
- Proposed approach with numbered steps
- Test plan
- Done when criteria
- Out of scope definition
- Notes for agent use