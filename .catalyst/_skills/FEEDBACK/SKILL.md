---
name: FEEDBACK
description: Analyzes conversation patterns with user, system feedback, agent reasoning processes, and overall workflow effectiveness to propose improvements for future interactions.
---

# FEEDBACK Skill

## Goal
Analyze user interactions, system feedback, agent reasoning processes, and overall workflow effectiveness to propose improvements that enhance the agent's learning capabilities and future performance without modifying project code or documentation directly.

## Purpose Clarification
This skill is designed for **meta-cognitive reflection** - the agent analyzes its own execution process to identify patterns of success vs. challenges in handling tasks, interactions with users, and integration of different skills. It focuses exclusively on improving the agent's capabilities rather than making changes to project code or documentation. It should not analyse only the FEEDBACK skill but look at all the elements that emerged from recent conversation.

## Analysis Framework
When executing this skill, use these structured approaches:
1. **Technical Pattern Analysis**: Identify technical challenges in execution (file handling, symlink recognition, tool usage)
2. **Communication Pattern Analysis**: Evaluate how well user intent was understood and responded to
3. **Skill Integration Analysis**: Assess when different skills worked well together vs. when they conflicted or were misapplied
4. **Workflow Efficiency Analysis**: Identify bottlenecks in the overall process
5. **Environmental Pattern Detection**: Identify tool-calling, LLM model, or environment quirks that should be captured in ENVIRONMENT.md

## Procedure
1. **Conversation Analysis**: Review the interaction history with user for patterns and insights about what worked well vs. what could be improved
2. **System Feedback Assessment**: Evaluate the execution process, examining how system responses were received and interpreted by the agent
3. **Agent Reasoning Review**: Analyze how the agent's own thinking process was applied during work and identify areas where reasoning could be more effective
4. **Overall Process Evaluation**: Assess effectiveness of the complete workflow system from an agent learning perspective rather than project modification
5. **Learning Pattern Identification**: Identify patterns in past interactions that indicate successful approaches vs. problematic ones for future improvement
6. **Improvement Proposal**: Generate reasoned recommendations focused on enhancing the agent's understanding and capabilities
7. **Knowledge Enhancement Planning**: Determine how insights should be incorporated into the agent's memory and learning system rather than project files

## When to Use
- After completing significant tasks where user feedback could help improve future interactions
- When wanting to enhance agent's understanding of what works well vs. what doesn't in terms of workflow effectiveness
- During reflection periods after complex workflows are completed for learning purposes
- When seeking systematic improvement based on actual execution experience that focuses on enhancing the agent rather than changing project components

## Examples
- Analyzing why certain documentation approaches work better than others from an agent learning perspective
- Reviewing how well different skills integrate with each other during actual use to improve skill selection decisions
- Assessing if the agent's reasoning process led to effective solutions and identifying where it could be more consistent or efficient
- Evaluating what user feedback suggests for improving future interaction patterns rather than project changes
- Extrapolating best practices from completed tasks to enhance the agent's ability to handle similar workflows in new interactions

## Feedback Integration
This skill integrates our recent workflow enhancements focused on learning:
- Template-based system usage in all components (but primarily for documentation)
- DateTime tracking throughout processes (DD/MM/YYYY HH:MM:SS)
- Documentation reference guidance for agent use
- Consistent formatting and cross-referencing patterns
- Updated knowledge base file structures

## Learning Pattern Capabilities
This skill now includes capability to:
1. Analyze past task completion patterns in `_agent/state/HANDOFF.md` to identify successful approaches vs. problematic ones
2. Identify successful interaction patterns from completed tasks to improve future workflow efficiency
3. Extract reusable lessons from `_agent/knowledge/MEMORY.md` that help the agent understand better approaches
4. Propose improved methodologies for future interactions based on historical evidence rather than project modifications
5. Extrapolate decision-making patterns that could inform improved agent behavior and approach selection

## Reflection Process
When executing this skill:
- Review conversation history to identify patterns that led to better outcomes vs. what didn't work well
- Analyze system responses to understand how the agent interpreted information effectively vs. where confusion or misinterpretation occurred
- Evaluate own reasoning process for consistency, efficiency and areas for improvement
- Propose improvements based on evidence from execution experience focused on enhancing agent knowledge and capabilities rather than project changes
- Extrapolate decision-making patterns from previous tasks to inform better future interaction approaches

## Template Reference
This skill follows [ACTIVE_TASK.template.md](../_templates/ACTIVE_TASK.template.md) format for consistency in documentation structure.
This skill references [DECISION.template.md](../_templates/DECISION.template.md) when extracting and proposing new architectural decisions (which are stored as lessons rather than implemented directly).

## Documentation Cross-reference
- [_agent/state/HANDOFF.md](../_agent/state/HANDOFF.md): Task completion records for learning patterns
- [_agent/knowledge/MEMORY.md](../_agent/knowledge/MEMORY.md): Reusable lessons database for agent improvement
- [_docs/decisions/DECISIONS.md](../_docs/decisions/DECISIONS.md): Architectural decisions documentation (for reference only)
- [_templates/DECISION.template.md](../_templates/DECISION.template.md): Decision template format (for reference only)