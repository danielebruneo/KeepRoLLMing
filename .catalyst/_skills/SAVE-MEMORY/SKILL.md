---
name: SAVE-MEMORY
description: Instructs the agent on where to save information in project documentation files according to CATALYST framework.
---

# SAVE-MEMORY Skill

## Goal
Instruct agents on how and where to properly store information in markdown documentation files within this repository's structure, following CATALYST framework conventions.

## Procedure
1. **Distinguish Memory Storage Locations**: 
   - For project-specific information: Save to `_agent/self/MEMORY.md` or other agent memory files
   - For persistent user preferences: Save to `~/.qwen/QWEN.md`

2. **Understand Context and Scope**:
   - Project-specific knowledge should be added to documentation files within the `_agent/`, `_project/`, or `_docs/` directories as appropriate
   - These locations are part of the CATALYST framework's knowledge management system

3. **Apply Framework Principles**:
   - Information saved in markdown files is used for agent-to-agent communication within a single repository context (for tasks like remembering project-specific facts, conventions, etc.)
   - This skill explicitly instructs agents to store information directly in documentation files rather than using built-in memory tools

## When to Use
- When agents need to remember specific facts about the current project's structure, conventions, or implementation details that should be stored in documentation files
- When persisting information for future reference within this repository through markdown documentation

## Examples
- Adding a note about how to run tests: `Add "npm run test" command to _agent/self/MEMORY.md` 
- Recording project-specific code conventions: `Document that we use TypeScript with React in the README`

## Memory Saving Rules
1. **Use markdown files within `_agent/`, `_project/`, or `_docs/` for repository-specific information**
2. **Store information directly in documentation files rather than built-in memory tools**
3. **Never mix internal agent operational notes with human-facing documentation**

## Skill Integration
This skill should be used in conjunction with other skills like:
- [LEARN](../LEARN/SKILL.md) when identifying new facts about the project to remember 
- [IMPLEMENT-FEEDBACK](../IMPLEMENT-FEEDBACK/SKILL.md) for remembering implementation details from feedback

## Modular Design Principles
This skill emphasizes proper data handling and organization in accordance with CATALYST principles:
- Integration with existing documentation workflow via markdown files
- Consistency with how other knowledge management skills work within the framework