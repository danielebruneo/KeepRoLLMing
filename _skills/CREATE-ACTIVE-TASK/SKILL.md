---
name: CREATE-ACTIVE-TASK
description: Creates or refreshes _agent/ACTIVE_TASK.md from the user request using a fixed task structure.
---

# CREATE-ACTIVE-TASK Skill

## Goal
Create or refresh [_agent/ACTIVE_TASK.md](../../_agent/ACTIVE_TASK.md) from the user request with clear, actionable definition.

## Procedure
1. Read the user request carefully - understand the exact scope and requirements.
2. Ask clarifying questions only if the task is too ambiguous to execute safely - avoid assumptions.
3. Reduce the request to one concrete active task - focus on single, well-defined objective.
4. Identify likely files if they are known - specify where changes will occur.
5. Write [_agent/ACTIVE_TASK.md](../../_agent/ACTIVE_TASK.md) using the template structure from `_templates/ACTIVE_TASK.template.md` - maintain consistent format.
6. Keep scope explicit and narrow - avoid broad or undefined tasks.

## When to Use
- When beginning a new task with user input
- When user gives specific instructions that need structured documentation
- When creating a focused, actionable task definition

## Examples
- Creating task for adding new model profile support (e.g., defining a new profile in config.yaml)
- Setting up documentation review task for specific files (e.g., reviewing README.md for accuracy)
- Defining refactoring work for code cleanup (e.g., improving function naming in app.py)

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