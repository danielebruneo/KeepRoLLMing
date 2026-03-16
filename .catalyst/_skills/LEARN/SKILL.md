---
name: LEARN
description: Implements systematic learning process that reviews project components, identifies improvement opportunities, and executes consolidation of knowledge enhancements.
---

# LEARN Skill

## Goal
Undergo through a comprehensive learning process that systematically reviews project components, identifies improvement opportunities, and executes consolidation of knowledge enhancements by leveraging existing skills effectively.

## Procedure
1. **Review Current State**: Examine all existing skills, documentation files, templates, and workflows using [REVIEW-DOC](../REVIEW-DOC/SKILL-REVIEW-DOC.md) and [UPDATE-KNOWLEDGE-BASE](../UPDATE-KNOWLEDGE-BASE/SKILL-UPDATE-KNOWLEDGE-BASE.md)
2. **Identify Improvement Opportunities**: Scan for inconsistencies, gaps, or areas where integration could be better
3. **Analyze Patterns**: Use [THINK](../THINK/SKILL-THINK.md) when needed to clarify what kind of issue is present, then look at how components interact using [FEEDBACK](../FEEDBACK/SKILL-FEEDBACK.md) and [IMPROVE-AGENT-WORKFLOW](../IMPROVE-AGENT-WORKFLOW/SKILL-IMPROVE-AGENT-WORKFLOW.md)
4. **Consolidate Improvements**: Group related enhancements into dedicated skills that make sense for agents to use, leveraging [IMPROVE-SKILLS](../IMPROVE-SKILLS/SKILL-IMPROVE-SKILLS.md) and [IMPROVE-DOC-STRUCTURES](../IMPROVE-DOC-STRUCTURES/SKILL-IMPROVE-DOC-STRUCTURES.md)
5. **Validate Implementation**: Ensure improvements maintain backward compatibility using [SAFE-REFACTOR](../SAFE-REFACTOR/SKILL-SAFE-REFACTOR.md) and validate with existing tests
6. **Document Learning**: Record lessons learned from this process for future reference, using [UPDATE-HUMAN-DOCS](../UPDATE-HUMAN-DOCS/SKILL-UPDATE-HUMAN-DOCS.md)
7. **Decide Outcome**: End with an explicit outcome: no action needed, proposal/TODO, recommend [ADAPT](../ADAPT/SKILL-ADAPT.md), or manual review
8. **Generate Final Report**: Produce a detailed report to the user summarizing what was learned, why it matters, whether adaptation is recommended, and outcomes achieved
9. **Create Learning Report File**: Generate a comprehensive learning report in _agent/learning_reports/ directory with structured data about improvements identified
10. **Prepare Commit Message**: Create appropriate commit message for documenting this learning session

## When to Use
- When seeking systematic improvement of agent workflow capabilities
- After multiple related changes have been implemented (like recent knowledge base updates)
- When maintaining or upgrading the entire system's knowledge base 
- During regular maintenance periods for continuous improvement
- Specifically after using UPDATE-KNOWLEDGE-BASE, IMPROVE-SKILLS, or other documentation-enhancing skills

## Examples of Learning Process Integration
- Reviewing all skills and documentation after implementing several improvements (like recent updates to MAP.md, COMMANDS.md)
- Consolidating scattered enhancements into dedicated skill categories using [IMPROVE-SKILLS](../IMPROVE-SKILLS/SKILL-IMPROVE-SKILLS.md)
- Analyzing integration patterns between different project components using [FEEDBACK](../FEEDBACK/SKILL-FEEDBACK.md)  
- Validating that new approaches maintain existing functionality with proper testing (like ensuring skill documentation remains accurate)

## Process Validation Requirements
When executing this skill:
- All improvements should maintain backward compatibility with existing functionality using [SAFE-REFACTOR](../SAFE-REFACTOR/SKILL-SAFE-REFACTOR.md)
- Template-based workflows are consistently applied across all components (ACTIVE_TASK.template.md, HANDOFF.template.md)
- DateTime tracking is properly implemented in all relevant files
- Cross-referencing between components is clear and logical

## Integration Verification Checkpoints
When running LEARN:
1. Verify that the skills being reviewed actually integrate properly with UPDATE-KNOWLEDGE-BASE 
2. Confirm IMPROVE-SKILLS correctly identifies which documentation references are consistent  
3. Ensure all cross-references in skill docs point to actual existing files
4. Validate that workflow makes logical sense across related skills
