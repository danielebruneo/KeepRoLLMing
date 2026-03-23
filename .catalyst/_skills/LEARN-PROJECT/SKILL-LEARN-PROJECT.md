---
name: LEARN-PROJECT
description: Helps agents explore and understand a project's structure, components, and knowledge base.
---

# LEARN-PROJECT Skill

## Goal
Help agents explore and understand a project by analyzing its codebase structure, deepening understanding of existing components, distilling key knowledge from various sources, and updating or creating project documentation and knowledge base entries.
Each time this skill gets invoked we should try do increase the knowledge around the project, going deeper or wider at each iteration. If you need to pick a subject, give preference to recent conversation if related topic were underdocumented, otherwise search for area of improvements.
So don't stop because you think the task is completed, learning never ends.

## Procedure
1. Analyze the codebase structure to identify core files and directories
2. Deepen understanding of existing components through file examination
3. Distill key knowledge from configuration files (composer.json, package.json, etc.)
4. Consolidate findings into project documentation
5. Update knowledge base files with distilled information

## When to Use
- When agents need comprehensive understanding of a project's architecture and technologies used
- When exploring unfamiliar codebases or modules
- When creating new documentation for project components

## Examples
- Analyzing the repository structure when starting work on a new feature
- Understanding dependencies in composer.json before implementing PHP functionality
- Creating initial knowledge base entries from existing project files

## Implementation Details

This skill will:
- Use `glob` searches to identify core files and directories 
- Read critical configuration files like `composer.json`, `package.json`
- Analyze main entry points and code structure patterns
- Consolidate findings into project documentation
- Update knowledge base files with distilled information

## Files Created or Modified

- `_project/KNOWLEDGE_BASE.md`: Updated with new understanding of the project
- `_agent/MAP.md`: Potentially updated to reflect project organization  
- Uses other skills like `INIT-KNOWLEDGE-BASE` and `UPDATE-KNOWLEDGE-BASE` for knowledge management

## Completed Rules
This task is completed if  new Knowledge was acquired and persisted along the process.

## Cross-reference

This skill is designed to work with:
- [INIT-KNOWLEDGE-BASE](../skills/INIT-KNOWLEDGE-BASE.md)
- [UPDATE-KNOWLEDGE-BASE](../skills/UPDATE-KNOWLEDGE-BASE.md)
