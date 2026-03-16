---
name: UPDATE-KNOWLEDGE-BASE
description: Refreshes the internal agent knowledge files after meaningful project changes.
---

# UPDATE-KNOWLEDGE-BASE Skill

## Goal
Refresh the internal knowledge files after the project changed.

## Procedure
1. Compare the current repository state with the existing docs - identify what's changed.
2. Update only the parts that are now stale - avoid unnecessary modifications.
3. Keep architecture docs durable and avoid task noise - focus on structural elements.
4. Keep [_agent/MAP.md](../../_agent/MAP.md) and [_agent/COMMANDS.md](../../_agent/COMMANDS.md) aligned with reality - ensure they reflect current state.

## When to Use
- After project structure or code changes that affect internal documentation
- When repository has been modified in ways that impact knowledge base
- When maintaining consistency between code and documentation

## Examples
- Updating MAP after adding new modules to the project (e.g., adding new directory entries to MAP.md)
- Refreshing COMMANDS when new scripts are added (e.g., updating COMMANDS.md with new test or build scripts)
- Aligning knowledge files with recent code refactoring (e.g., updating MAP.md to reflect new module locations)

## Skill Integration
This skill works best when integrated with other system skills:
- **LEARN**: Can leverage this skill during systematic learning processes for knowledge base updates 
- **REVIEW-DOC**: Should work alongside documentation review process to ensure consistency
- **IMPROVE-SKILLS**: Integrates with skill enhancement process to keep all documentation consistent

## Modular Design Principles
This skill follows modular design principles:
- Focuses on updating knowledge files rather than re-implementing functionality 
- Maintains clear separation between what each skill does vs. what it references  
- Promotes reuse of existing system capabilities through proper integration
- Ensures knowledge base stays current without duplicating efforts

## Knowledge Base Enhancement
This skill now includes enhanced functionality for:
- Template-based workflow system integration
- DateTime tracking in all knowledge files
- Consistent formatting across all components
- Cross-referencing between project elements