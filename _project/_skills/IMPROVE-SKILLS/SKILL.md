---
name: IMPROVE-SKILLS
description: Reviews and enhances existing skills in the project to improve clarity, usability, and actionable guidance for Qwen Code agents.
---

# IMPROVE-SKILLS Skill

## Goal
Review existing skills in this CATALYST-based project and enhance their documentation to make them more clear, usable, and actionable for Qwen Code agents. This skill focuses on improving consistency, integration points, file references, and overall clarity of implementation guidance.

## Procedure
1. **Analyze current skill documentation** - Examine all existing skill definitions in the layered CATALYST structure:
   - `_agent/overlay/_skills/` (local overrides)
   - `_project/_skills/` (project-specific skills) 
   - `.catalyst/_skills/` (core skills)

2. **Identify improvement opportunities** by reviewing for:
   - Ambiguous or unclear descriptions
   - Missing integration examples with other skills
   - Inconsistent file path references
   - Outdated procedure steps or workflow patterns

3. **Propose specific enhancements** to skill definitions including:
   - Concrete implementation examples
   - Clearer integration points with related skills
   - Consistent formatting and cross-referencing patterns
   - Better task decomposition guidance

4. **Update skill documentation** with:
   - Enhanced clarity in procedural steps
   - Specific examples of usage for each skill
   - Clear integration references to other CATALYST skills (e.g., LEARN, WORK, PLAN)
   - Consistent file path and reference patterns

5. **Ensure consistency across all skills by**:
   - Verifying correct file path references throughout using consistent relative paths like `../../` 
   - Confirming integration patterns work properly with related skills
   - Checking that cross-references point to actual files in the layered structure

6. **Test integration between related skills** through example workflows and validation:
   - Run a test sequence using multiple integrated skills
   - Verify they interact as documented without conflicts or broken references

## When to Use
- When reviewing project skills for improvement opportunities (especially in complex multi-skill workflows)
- When enhancing documentation to make skills more usable by agents
- After identifying gaps or ambiguities in existing skill definitions, particularly those that need context from related skills
- During regular maintenance when knowledge base consistency should be verified
- Before implementing major system changes that might affect skill functionality or integration points

## Examples of Specific Improvements (Project Context)
### For this repository:
1. **Integration with other skills** - Ensure all references are consistent, such as:
   - [WORK](../_skills/WORK/SKILL.md) for how tasks flow through the system
   - [PLAN](../_skills/PLAN/SKILL.md) when implementing complex changes that require planning

2. **Clearer file path handling** - Use consistent patterns like `../../` rather than ambiguous references:
   - When referencing documents in `_agent/state/`, use correct relative paths
   - When referencing files in project root, standardize on absolute or relative paths consistently

3. **Enhanced procedural clarity** - Provide better context for actions such as:
   - Reviewing CATALYST core skills vs project-specific ones to ensure proper consistency 
   - Applying datetime tracking (DD/MM/YYYY HH:MM:SS) when referencing files that use it
   - Maintaining consistent naming patterns in skill directories

## Integration Verification Process
When reviewing skills for improvements:
1. **Verify file path references** are consistent and correctly formatted using relative paths like `../..` to navigate the layered structure properly
2. **Confirm integration points with related skills** work as expected by following their procedures 
3. **Check cross-referenced documentation files exist** in their specified locations (e.g., all referenced templates, docs)
4. **Validate skill workflows make logical sense** when viewed within the full CATALYST framework

## Knowledge Base Integration
This skill integrates with the broader CATALYST workflow by:
- Supporting consistent formatting across project skills and knowledge base components (_agent/MAP.md, _agent/COMMANDS.md)
- Applying template-based system enhancements to match CATALYST conventions 
- Maintaining uniform datetime tracking (DD/MM/YYYY HH:MM:SS) in all reference documentation
- Ensuring cross-referencing patterns are clear and maintainable for future development

## Workflow Enhancement Features Implemented
This skill enables consistent improvements across the project's agent workflow including:
1. **Template-based system integration** - Using standard CATALYST template conventions consistently  
2. **DateTime tracking implementation** - Applying DD/MM/YYYY HH:MM:SS format to all relevant documentation files
3. **Consistent cross-referencing patterns** - Ensuring references use proper relative paths and are validated for existence
4. **Enhanced skill structure guidance** - Providing clear, actionable procedural steps to make skills more implementable by agents

## Integration Points with Other Skills in This Project
This skill works closely with:
- [WORK](../_skills/WORK/SKILL.md) - when implementing improvements through a complete workflow cycle
- [PLAN](../_skills/PLAN/SKILL.md) - for creating execution plans before making substantial changes to skills 
- [LEARN](../_skills/LEARN/SKILL.md) - when discovering learning opportunities during skill review processes

## Template Reference in This Project
This skill follows the same pattern as other project skills:
1. Uses consistent relative paths like `../_skills/...` for cross-references within CATALYST structure
2. Maintains datetime tracking format: DD/MM/YYYY HH:MM:SS 
3. Follows standard naming conventions with SKILL.md files in directories
4. Integrates with the layered repository approach from .catalyst/_skills, _agent/overlay/_skills and _project/_skills

## Expected Output
After execution:
1. Improved skill documentation that is clearer and more actionable  
2. Consistent integration points across all project skills
3. Enhanced cross-referencing patterns with correct relative paths
4. Documentation that follows the layered CATALYST structure properly
5. Updated skill files in `_project/_skills/` directory with better examples and guidance

## Relationship with Other Skills (In this Project)
- [THINK](../_skills/THINK/SKILL.md) - For when unclear about which skills need improvement or how to approach it
- [PLAN](../_skills/PLAN/SKILL.md) - When more detailed planning is needed before implementing skill improvements 
- [LEARN](../_skills/LEARN/SKILL.md) - If there are learning opportunities discovered during the review process
- [WORK](../_skills/WORK/SKILL.md) - For executing the full workflow cycle of improving skills

Note: This skill should be executed as a single workflow unit, not broken into multiple steps.