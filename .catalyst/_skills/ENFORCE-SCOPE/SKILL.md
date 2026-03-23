---
name: ENFORCE-SCOPE
description: Validates that file changes comply with current work scope defined in SCOPE.md.
---

# ENFORCE-SCOPE Skill

## Goal
Validate that file modifications during work comply with the current scope defined in `_agent/state/SCOPE.md`.

## Purpose
This skill prevents scope creep by enforcing boundaries on what types of files can be modified during a task. It ensures agents stay focused on their assigned scope and don't accidentally modify unrelated parts of the codebase.

## Procedure

### 1. Read Current Scope
- Read `_agent/state/SCOPE.md` to understand boundaries
- Parse scope definition:
  - Allowed file patterns
  - Excluded file patterns
  - Allowed modification types
  - Related tasks or features

### 2. Track File Changes
- Monitor all files being modified
- For each file:
  - Check if it matches allowed patterns
  - Verify it's not in excluded patterns
  - Validate modification type is allowed

### 3. Validate Compliance
For each file being modified:
- **Within scope:** Log as approved
- **Outside scope:** Generate warning
  - Explain why it's outside scope
  - Suggest: UPDATE-SCOPE or skip file
- **Critical violation:** Block change (config, tests-only tasks shouldn't touch implementation)

### 4. Generate Report
- List all validated files
- Flag any scope violations
- Suggest corrective actions

### 5. Update HANDOFF.md (if violations)
- Log scope validation results
- Note any violations and how they were handled
- Reference SCOPE.md for context

## When to Use
- Before modifying files during work
- When user requests changes to new files
- At end of work session to validate all changes
- When using USE-CATALYST (auto-invoked)
- Before committing changes

## Constraints
- Never allow scope violations without explicit override
- Always reference SCOPE.md in validation report
- Log all files modified (even if within scope)
- Suggest UPDATE-SCOPE if legitimate work requires scope extension
- Block critical violations (e.g., tests→implementation changes)

## Example Usage

### Scope Definition (SCOPE.md)
```markdown
# Agent Scope

## Current Scope
Testing only - no implementation changes

## Status
active

## Source: PICKUP-TASK
Task: Add comprehensive test coverage

## Allowed Files
- tests/
- keeprollming/test_*.py
- _agent/state/

## Excluded Files
- keeprollming/*.py (implementation)
- config.yaml
- requirements.txt

## Why
Focus on test coverage without affecting implementation

## Related Task
_active_task: _agent/state/ACTIVE_TASK.md
```

### Validation Report
```
🔍 SCOPE VALIDATION REPORT
============================

✅ Within scope:
  - tests/test_routing.py (modified)
  - tests/test_config.py (modified)

⚠️  Outside scope:
  - keeprollming/routing.py (implementation)
    → Suggestion: UPDATE-SCOPE or skip this file
  - config.yaml (configuration)
    → Suggestion: UPDATE-SCOPE or skip this file

🛑 Critical violation:
  - keeprollming/app.py (core implementation)
    → Blocking: Tests-only scope should not modify core implementation
    → Suggestion: Create new task with proper scope or UPDATE-SCOPE
```

### Handling Violations

#### Option 1: Skip File
```
Agent: "This file is outside current scope. Skipping keeprollming/app.py"
```

#### Option 2: UPDATE-SCOPE
```
Agent: "This requires scope extension. Calling UPDATE-SCOPE..."
skill: "UPDATE-SCOPE"
  new_scope: "Testing + configuration updates"
  allowed_files: "tests/, config.yaml"
```

## Related Skills
- **[UPDATE-SCOPE]** - Update scope boundaries
- **[WORKFLOW-AUDIT]** - Comprehensive validation including scope
- **[USE-CATALYST]** - Auto-invokes scope enforcement
- **[PICKUP-TASK]** - Sets initial scope
- **[CLOSE-TASK]** - Records scope adherence in handoff

## Integration with Workflow
```
1. Work on task with defined scope
2. Modify file → ENFORCE-SCOPE checks
   - If within scope: ✅ Continue
   - If outside scope: ⚠️ Warn or block
3. Update SCOPE.md if needed via UPDATE-SCOPE
4. Complete task with scope adherence report
```

## DateTime Tracking
All timestamps use format: `DD/MM/YYYY HH:MM:SS`

## Template Reference
Scope definitions follow the structure in `_agent/state/SCOPE.md`.