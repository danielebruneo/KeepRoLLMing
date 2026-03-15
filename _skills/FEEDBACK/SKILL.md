---
name: FEEDBACK
description: Analyzes conversation patterns with user, system feedback, agent reasoning processes, and overall workflow effectiveness to propose improvements. 
---

# FEEDBACK Skill

## Goal
Analyze user interactions, system feedback, agent reasoning processes, and overall workflow effectiveness to propose and implement meaningful improvements.

## Procedure  
1. **Conversation Analysis**: Review the interaction history with user for patterns and insights
2. **System Feedback Assessment**: Evaluate what emerges from the execution process 
3. **Agent Reasoning Review**: Analyze how the agent's own thinking process was applied during work
4. **Overall Process Evaluation**: Assess effectiveness of the complete workflow system  
5. **Decision Extrapolation**: Identify patterns in past interactions to extrapolate better approaches for future tasks
6. **Improvement Proposal**: Generate reasoned recommendations for better outcomes
7. **Implementation Planning**: Determine next steps based on analysis

## When to Use
- After completing significant tasks where user feedback could be valuable
- When wanting to improve agent's understanding of what works and what doesn't
- During reflection periods after complex workflows are completed
- When seeking systematic improvement based on actual execution experience

## Examples
- Analyzing why certain documentation approaches work better than others  
- Reviewing how well different skills integrate with each other during actual use
- Assessing if the agent's reasoning process led to effective solutions
- Evaluating what user feedback suggests for future improvements
- Extrapolating best practices from completed tasks to improve efficiency in new workflows

## Feedback Integration
This skill integrates our recent workflow enhancements:
- Template-based system usage in all components 
- DateTime tracking throughout processes (DD/MM/YYYY HH:MM:SS)
- Documentation reference guidance for agent use  
- Consistent formatting and cross-referencing patterns
- Updated knowledge base file structures

## Decision Extrapolation Capabilities
This skill now includes capability to:
1. Analyze past task completion patterns in `_agent/HANDOFF.md` 
2. Identify successful approaches from completed tasks
3. Extract reusable lessons from `_agent/MEMORY.md`
4. Propose improved methodologies for future work based on historical evidence
5. Extrapolate decision-making patterns that could inform new architectural decisions

## Reflection Process
When executing this skill:
- Review conversation history to identify patterns that led to better outcomes
- Analyze system responses to understand what worked well vs. what didn't 
- Evaluate own reasoning process for consistency and effectiveness
- Propose improvements based on evidence from the execution experience
- Extrapolate decision-making patterns from previous tasks to inform current work

## Template Reference
This skill follows [ACTIVE_TASK.template.md](../_templates/ACTIVE_TASK.template.md) format for consistency in documentation structure.
This skill references [DECISION.template.md](../_templates/DECISION.template.md) when extracting and proposing new architectural decisions.

## Documentation Cross-reference
- [_agent/HANDOFF.md](../_agent/HANDOFF.md): Task completion records  
- [_agent/MEMORY.md](../_agent/MEMORY.md): Reusable lessons database
- [_docs/decisions/DECISIONS.md](../_docs/decisions/DECISIONS.md): Architectural decisions documentation
- [_templates/DECISION.template.md](../_templates/DECISION.template.md): Decision template format