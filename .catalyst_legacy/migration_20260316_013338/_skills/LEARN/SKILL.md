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
7. **Decide Outcome**: End with an explicit outcome: no action needed, proposal/TODO, invoke [THINK](../THINK/SKILL-THINK.md), recommend [ADAPT](../ADAPT/SKILL-ADAPT.md), or manual review
8. **Generate Final Report**: Produce a detailed report to the user summarizing what was learned, why it matters, whether adaptation is recommended, and outcomes achieved
9. **Create Learning Report File**: Generate a comprehensive learning report in _agent/learning_reports/ directory with structured data about improvements identified
10. **Prepare Commit Message**: Create appropriate commit message for documenting this learning session

## When to Use
- When seeking systematic improvement of agent workflow capabilities
- After multiple related changes have been implemented
- When maintaining or upgrading the entire system's knowledge base
- During regular maintenance periods for continuous improvement

## Examples
- Reviewing all skills and documentation after implementing several improvements using [REVIEW-DOC](../REVIEW-DOC/SKILL-REVIEW-DOC.md)
- Consolidating scattered enhancements into dedicated skill categories using [IMPROVE-SKILLS](../IMPROVE-SKILLS/SKILL-IMPROVE-SKILLS.md)
- Analyzing integration patterns between different project components using [FEEDBACK](../FEEDBACK/SKILL-FEEDBACK.md)
- Validating that new approaches maintain existing functionality with proper testing

## Skill Integration
This skill is designed to leverage other skills in the system:
- **REVIEW-DOC**: For examining and validating documentation accuracy
- **UPDATE-KNOWLEDGE-BASE**: For maintaining knowledge files consistency
- **FEEDBACK**: For analyzing patterns from execution experience
- **IMPROVE-SKILLS**: For enhancing existing skill documentation quality
- **IMPROVE-DOC-STRUCTURES**: For organizing document structure improvements
- **SAFE-REFACTOR**: For ensuring changes are safe and backward compatible

## Modular Design Principles
This skill follows modular design principles:
- Leverages specialized skills for specific functions rather than trying to do everything itself
- Maintains loose coupling between components while enabling tight integration when needed
- Promotes reusability of existing functionality through proper referencing
- Ensures each skill has clear responsibility and scope

## Process Validation Requirements
When executing this skill:
- All improvements should maintain backward compatibility with existing functionality using [SAFE-REFACTOR](../SAFE-REFACTOR/SKILL-SAFE-REFACTOR.md)
- Template-based workflows are consistently applied across all components (ACTIVE_TASK.template.md, HANDOFF.template.md)
- DateTime tracking is properly implemented in all relevant files
- Cross-referencing between components is clear and logical

## Orchestration policy
`LEARN` is the high-level entry point for self-improvement. It should:
- use [FEEDBACK](../FEEDBACK/SKILL-FEEDBACK.md) for recent, local friction
- use [IMPLEMENT-FEEDBACK](../IMPLEMENT-FEEDBACK/SKILL-IMPLEMENT-FEEDBACK.md) only for bounded, concrete improvements
- use [SYNC-BOOTSTRAP-FILES](../SYNC-BOOTSTRAP-FILES/SKILL-SYNC-BOOTSTRAP-FILES.md) when bootstrap drift is detected
- prefer consolidation and pruning over adding more documentation by default

When running as periodic maintenance, `LEARN` should leave the repository in a more compact and better-aligned state.

## File Generation Details
The LEARN skill will create learning report files in:
- `_agent/learning_reports/` directory (created if it doesn't exist)
- Each report file named with timestamp: `learn_session_YYYYMMDD_HHMMSS.md`

## Commit Message Format
When completing a LEARN session, the commit message follows this format:
```
CATALYST Learning session [datetime]: Summary of improvements identified and implemented

This session reviewed all project components to identify enhancement opportunities:
- Updated knowledge base with current understanding
- Applied template-based workflow consistently 
- Maintained datetime tracking throughout process (DD/MM/YYYY HH:MM:SS)
- Consolidated lessons learned from project work
```


## Outcome policy
LEARN should not automatically perform broad modifications. It should end in one of these outcomes:
1. **No action needed**
2. **Create or update a TODO / proposal**
3. **Invoke THINK** when the correct adaptation path is still unclear
4. **Recommend ADAPT** for a small, low-risk CATALYST change
5. **Recommend manual review** when the issue is architectural or cross-cutting

## Relationship with THINK and ADAPT
- Use [THINK](../THINK/SKILL-THINK.md) if the correct learning target is unclear.
- Prefer [ADAPT](../ADAPT/SKILL-ADAPT.md) only when the improvement is local, safe, and scope-appropriate.
- When the current scope is `CATALYST` or `META`, repository-side changes to CATALYST skills and workflow docs count as valid learning outcomes.
- `LEARN` should not interrupt active KeepRoLLMing work unless the scope explicitly includes CATALYST or META.
