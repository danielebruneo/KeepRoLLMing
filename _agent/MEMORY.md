# Memory

Use this file for non-obvious lessons that are likely to matter again.

## Template Reference
This file follows the [MEMORY.template.md](../_templates/MEMORY.template.md) format for consistency.

## Topic: Template utilization in documentation
- Date/session: 15/03/2026 10:21:59
- Lesson: When creating new documentation elements, ensure they follow the established template format and cross-reference related components properly.
- Relevant files: _templates/ACTIVE_TASK.template.md, _docs/development/WORKFLOW.md
- Category: Documentation Pattern

## Topic: Symlink handling in skill directories
- Date/session: 15/03/2026 12:47:00
- Lesson: When working with skills in CATALYST projects, be aware that some files like SKILL-FEEDBACK.md are symlinks to SKILL.md. Only edit the actual content file (SKILL.md) as both links point to the same content. Always run 'ls -la' first to check for symlink relationships before making any modifications.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _skills/FEEDBACK/SKILL.md
- Category: System Pattern

## Topic: Skill integration decision-making
- Date/session: 15/03/2026 14:30:00
- Lesson: When multiple skills are involved in a task, evaluate whether they complement each other or conflict. Consider the skill's primary purpose and how it interacts with others.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _skills/IMPROVE-SKILLS/SKILL-IMPROVE-SKILLS.md
- Category: Decision Pattern

## Topic: Workflow efficiency analysis
- Date/session: 15/03/2026 15:15:00
- Lesson: Identify bottlenecks in task execution by analyzing time spent on different activities. Look for patterns where the same tasks are performed repeatedly with similar inefficiencies.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _agent/HANDOFF.md
- Category: Process Pattern

## Topic: Comprehensive API documentation approach
- Date/session: 15/03/2026 14:30:00
- Lesson: When documenting API endpoints, it's important to cover all parameters including optional ones, response formats for both streaming and non-streaming modes, usage examples with different profiles, and environment variables that affect behavior. The documentation should be comprehensive enough for developers to understand how to use the system effectively.
- Relevant files: _docs/API_DOCUMENTATION.md, README.md
- Category: Documentation Pattern

## Topic: Bootstrap redundancy for agent entrypoints
- Date/session: 15/03/2026 14:50:00
- Lesson: Treat QWEN.md as the runner-specific bootstrap loader, AGENTS.md as the canonical workflow, and README.md as human-facing redundancy. Keeping all three aligned makes CATALYST more resilient when runner-specific files are regenerated.
- Relevant files: QWEN.md, AGENTS.md, README.md
- Category: System Pattern

## Topic: Runtime boundary for tool schemas
- Date/session: 15/03/2026 14:52:00
- Lesson: Repository documentation should not redefine runtime tool schemas. When local docs tried to document tool parameters, agent behavior regressed. Keep workflow guidance in repo docs, and leave tool contracts to the runtime.
- Relevant files: AGENTS.md, QWEN.md
- Category: System Pattern
