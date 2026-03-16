---
name: REMEMBER
description: Persist important insights and learning patterns in the appropriate CATALYST memory storage locations using a structured approach.
---

# REMEMBER Skill

## Goal
Persist important insights and learning patterns in the appropriate CATALYST memory storage locations using a structured approach.

## Procedure  
1. **Identify Learning Type**: Determine if the insight is about implementation details, code patterns, or general best practices
2. **Select Appropriate Location**: Based on the type of information:
   - For project-specific insights and patterns: Save to `_agent/knowledge/MEMORY.md` 
   - For broader architectural knowledge: Save to `_docs/decisions/DECISIONS.md`
   - For task execution learning: Save to `_agent/state/HANDOFF.md` or similar agent state files
3. **Use Clear Structure**: Apply consistent formatting and categorization for each type of knowledge
4. **Document Learning Meaningfully** - Include rationale, pattern details, and implementation guidance

## When to Use
- After implementing features that introduce new patterns or approaches 
- When learning reusable practices from the implementation process  
- For documenting lessons that will help future implementations in similar areas
- During complex implementation phases where systematic knowledge capture is needed

## Examples of Knowledge Persistence
- "Remember how to distinguish between file paths and direct text content in configuration"
- "Document the pattern for handling custom summary prompts" 
- "Capture lessons about YAML parsing nuances"

## Key Memory Locations by Scope:
- `_skills/REMEMBER/SKILL-REMEMBER.md` - Documentation location for this skill (this file)
- `_agent/knowledge/MEMORY.md` - Project-specific implementation insights
- `_docs/decisions/DECISIONS.md` - Architectural decision patterns  
- `_agent/state/HANDOFF.md` - Task execution learnings

The REMEMBER skill ensures that important learning from CATALYST implementation is stored in a consistent, discoverable way to enhance future development cycles.