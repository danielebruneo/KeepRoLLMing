# Skills Index

**Generated:** 2026-03-17 17:07:25

## Overview

This index provides a comprehensive map of all available CATALYST skills, organized by functional category. Use this reference to quickly discover and leverage the appropriate skill for your task.

## Quick Reference

| Category | Skills Count |
|----------|--------------|
| Task Management & Workflow | 9 |
| Documentation & Knowledge | 23 |
| Repository Maintenance | 3 |
| Code Quality & Testing | 4 |
| Catalyst Core Operations | 8 |
| Feature Development | 0 |

## Detailed Categories

### Task Management & Workflow

- **[CLOSE-TASK]**(.catalyst/_skills/CLOSE-TASK/SKILL.md) - Closes or rolls over the active task by updating handoff, memory, and task state.
- **[COMPLETED-TASKS]**(.catalyst/_skills/COMPLETED-TASKS/SKILL.md) - Maintains a record of completed tasks for historical tracking and project metrics.
- **[CREATE-ACTIVE-TASK]**(.catalyst/_skills/CREATE-ACTIVE-TASK/SKILL.md) - Creates or refreshes _agent/ACTIVE_TASK.md from the user request using a fixed task structure.
- **[ENFORCE-PROJECT-TODO]**(_project/_skills/ENFORCE-PROJECT-TODO/SKILL.md) - No description available
- **[PICKUP-TASK]**(.catalyst/_skills/PICKUP-TASK/SKILL.md) - Selects and converts a task from the TODO list into an active task for execution.
- **[PLAN]**(.catalyst/_skills/PLAN/SKILL.md) - Create a bounded execution plan for a non-trivial or underspecified task before implementation.
- **[SYNC-QWEN-SKILL-REGISTRY]**(.catalyst/_skills/SYNC-QWEN-SKILL-REGISTRY/SKILL.md) - Rebuild .qwen/skills as a runtime projection of active markdown skills from overlay, project, and core locations.
- **[UPDATE-TODO]**(.catalyst/_skills/UPDATE-TODO/SKILL.md) - Updates and maintains the TODO list with new items, status changes, and completion tracking.
- **[WORK]**(.catalyst/_skills/WORK/SKILL.md) - Automates the full workflow of checking active tasks, picking up tasks when needed, implementing them through completion, and properly closing completed tasks with commit and handoff updates.

### Documentation & Knowledge

- **[CONSOLIDATE-DOC]**(.catalyst/_skills/CONSOLIDATE-DOC/SKILL.md) - No description available
- **[CONSOLIDATE-DOC]**(_project/_skills/CONSOLIDATE-DOC/SKILL.md) - No description available
- **[CONSOLIDATE-DOCS]**(.catalyst/_skills/CONSOLIDATE-DOCS/SKILL.md) - Merge durable knowledge into canonical docs and remove duplication between agent notes, docs, and skills.
- **[DISTILL-LEARNINGS]**(.catalyst/_skills/DISTILL-LEARNINGS/SKILL.md) - Convert raw feedback, handoff notes, and repeated observations into compact reusable lessons and clear destinations.
- **[DOCUMENTATION-REFERENCE]**(.catalyst/_skills/DOCUMENTATION-REFERENCE/SKILL.md) - Helps agents understand when and how to reference specific documentation files during their workflows.
- **[ENFORCE-PROJECT-WORKFLOW]**(_project/_skills/ENFORCE-PROJECT-WORKFLOW/SKILL.md) - No description available
- **[IMPROVE-AGENT-WORKFLOW]**(.catalyst/_skills/IMPROVE-AGENT-WORKFLOW/SKILL.md) - Improves the overall agent workflow system by integrating knowledge base, documentation, and skill structures.
- **[IMPROVE-DOC]**(.catalyst/_skills/IMPROVE-DOC/SKILL.md) - No description available
- **[IMPROVE-DOC]**(_project/_skills/IMPROVE-DOC/SKILL.md) - No description available
- **[IMPROVE-DOC-STRUCTURES]**(.catalyst/_skills/IMPROVE-DOC-STRUCTURES/SKILL.md) - Improves the structure and consistency of documentation files across the project.
- **[IMPROVE-SKILLS]**(.catalyst/_skills/IMPROVE-SKILLS/SKILL.md) - Reviews and enhances existing skills in the project to improve clarity, usability, and actionable guidance for Qwen Code agents.
- **[INIT-KNOWLEDGE-BASE]**(.catalyst/_skills/INIT-KNOWLEDGE-BASE/SKILL.md) - Initializes the internal agent knowledge files by inferring project structure, commands, and architecture from the current repository.
- **[LEARN]**(.catalyst/_skills/LEARN/SKILL.md) - Implements systematic learning process that reviews project components, identifies improvement opportunities, and executes consolidation of knowledge enhancements.
- **[LEARN-PROJECT]**(.catalyst/_skills/LEARN-PROJECT/SKILL.md) - Helps agents explore and understand a project's structure, components, and knowledge base.
- **[PRUNE-KNOWLEDGE]**(.catalyst/_skills/PRUNE-KNOWLEDGE/SKILL.md) - Identify stale, duplicated, or overgrown knowledge and trim it without losing important durable information.
- **[REVIEW-DOC]**(.catalyst/_skills/REVIEW-DOC/SKILL.md) - Reviews markdown documentation to keep it accurate, non-redundant, and aligned with the current project state.
- **[SAVE-MEMORY]**(.catalyst/_skills/SAVE-MEMORY/SKILL.md) - Instructs the agent on where to save information in project documentation files according to CATALYST framework.
- **[SYNC-BOOTSTRAP-FILES]**(.catalyst/_skills/SYNC-BOOTSTRAP-FILES/SKILL.md) - Keeps QWEN.md, AGENTS.md, and README.md aligned so the CATALYST bootstrap survives regeneration and documentation drift.
- **[THINK]**(.catalyst/_skills/THINK/SKILL.md) - Pause execution to clarify objective, scope, and the most appropriate next skill without modifying files.
- **[UPDATE-HUMAN-DOCS]**(.catalyst/_skills/UPDATE-HUMAN-DOCS/SKILL.md) - Updates human-facing project documentation without mixing it with internal agent operational notes.
- **[UPDATE-KNOWLEDGE-BASE]**(.catalyst/_skills/UPDATE-KNOWLEDGE-BASE/SKILL.md) - Refreshes the internal agent knowledge files after meaningful project changes.
- **[UPDATE-README]**(.catalyst/_skills/UPDATE-README/SKILL.md) - Safely updates README.md when project-level usage, status, or structure has materially changed.
- **[UPDATE-README-SAFE]**(.catalyst/_skills/UPDATE-README-SAFE/SKILL.md) - Update the public README without damaging its human-oriented role.

### Repository Maintenance

- **[BUILD-REPO-MAP]**(.catalyst/_skills/BUILD-REPO-MAP/SKILL.md) - Builds or refreshes _agent/MAP.md by inferring the real repository structure and responsibilities.
- **[INDEX-SKILLS]**(.catalyst/_skills/INDEX-SKILLS/SKILL.md) - Generate and maintain a comprehensive SKILLS-INDEX.md file in _project/_skills/ that maps all available skills grouped by topic, making it easy for agents to discover and leverage the full skill set.
- **[SYNC-COMMANDS]**(.catalyst/_skills/SYNC-COMMANDS/SKILL.md) - Refreshes _agent/COMMANDS.md from the actual repository scripts, tooling, and test commands.

### Code Quality & Testing

- **[ADD-FEATURE]**(.catalyst/_skills/ADD-FEATURE/SKILL.md) - Implements a requested feature in a controlled, test-aware way without broad unrelated changes.
- **[FIX-FAILING-TEST]**(.catalyst/_skills/FIX-FAILING-TEST/SKILL.md) - Restores intended behavior for a failing test using the smallest safe code or test change.
- **[IMPLEMENT-FEEDBACK]**(.catalyst/_skills/IMPLEMENT-FEEDBACK/SKILL.md) - Applies a bounded set of improvements discovered through FEEDBACK when they are concrete, low-risk, and clearly beneficial.
- **[SAFE-REFACTOR]**(.catalyst/_skills/SAFE-REFACTOR/SKILL.md) - Refactors code while preserving behavior and keeping scope tightly controlled.

### Catalyst Core Operations

- **[ADAPT]**(.catalyst/_skills/ADAPT/SKILL.md) - Apply a small, safe, local improvement to the CATALYST workflow after clear feedback or learning.
- **[DIGEST-LEGACY-CATALYST]**(.catalyst/_skills/DIGEST-LEGACY-CATALYST/SKILL.md) - Digest archived legacy CATALYST assets under .catalyst_legacy and redistribute information units into the new layered structure.
- **[ENFORCE-CATALYST-WORKFLOW]**(_project/_skills/ENFORCE-CATALYST-WORKFLOW/SKILL.md) - No description available
- **[FEEDBACK]**(.catalyst/_skills/FEEDBACK/SKILL.md) - Analyzes conversation patterns with user, system feedback, agent reasoning processes, and overall workflow effectiveness to propose improvements for future interactions.
- **[PROPOSE-CATALYST-CORE-CHANGE]**(.catalyst/_skills/PROPOSE-CATALYST-CORE-CHANGE/SKILL.md) - Capture a local adaptation that may be worth promoting into the CATALYST core.
- **[PROPOSE-CATALYST-PULL-REQUEST]**(.catalyst/_skills/PROPOSE-CATALYST-PULL-REQUEST/SKILL.md) - Prepare a structured pull-request proposal for promoting a local improvement into the upstream CATALYST core.
- **[RECONCILE-LEGACY-SKILLS]**(.catalyst/_skills/RECONCILE-LEGACY-SKILLS/SKILL.md) - Analyze legacy skills and reconcile them with the new layered CATALYST skill model.
- **[REMEMBER]**(.catalyst/_skills/REMEMBER/SKILL.md) - Persist important insights and learning patterns in the appropriate CATALYST memory storage locations using a structured approach.

## Usage Guidelines

### How to Use Skills

1. **Identify Your Task**: Determine what type of work you need to accomplish
2. **Find the Category**: Locate the relevant category in this index
3. **Select a Skill**: Choose the skill that best matches your needs
4. **Invoke the Skill**: Use `skill: "SKILL-NAME"` to execute it

### Best Practices

- Always read the SKILL.md file for detailed instructions before using a skill
- Skills are markdown procedures, not executables - they guide agent behavior
- Use `INDEX-SKILLS` skill when adding new skills to update this index
- Check `_agent/state/ACTIVE_TASK.md` for context on current work

## Skill Categories Explained

**Task Management & Workflow**: Skills for managing tasks, TODOs, and workflow progression
**Documentation & Knowledge**: Skills for creating, improving, and maintaining documentation
**Repository Maintenance**: Skills for keeping the repository structure and metadata up to date
**Code Quality & Testing**: Skills for refactoring code and fixing tests
**Catalyst Core Operations**: Skills for managing CATALYST framework itself
**Feature Development**: Skills for implementing new features

## Maintenance

This index is auto-generated by the **INDEX-SKILLS** skill. When adding or modifying skills:
1. Create/update the SKILL.md file in the appropriate directory
2. Run the INDEX-SKILLS skill to regenerate this index
3. Verify that all skills are correctly categorized

---
*Generated by INDEX-SKILLS on 2026-03-17 17:07:25*