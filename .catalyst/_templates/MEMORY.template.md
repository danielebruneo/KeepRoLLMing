# Memory

Use this file for non-obvious lessons that are likely to matter again.

## Entry format
- Date/session: DD/MM/YYYY HH:MM:SS
- Topic: [short description]
- Lesson: [detailed explanation of the lesson learned]
- Relevant files: [path/to/file](path/to/file)
- Category: [learning type] (optional)

## Template Reference
This file follows the [MEMORY.template.md](../_templates/MEMORY.template.md) format for consistency.

## Example entry
- Date/session: 15/03/2026 10:21:59
- Topic: Template utilization in documentation
- Lesson: When creating new documentation elements, ensure they follow the established template format and cross-reference related components properly.
- Relevant files: _templates/ACTIVE_TASK.template.md, _docs/development/WORKFLOW.md
- Category: Documentation Pattern

## Example entry
- Date/session: 15/03/2026 12:47:00
- Topic: Symlink handling in skill directories
- Lesson: When working with skills in CATALYST projects, be aware that some files like SKILL-FEEDBACK.md are symlinks to SKILL.md. Only edit the actual content file (SKILL.md) as both links point to the same content. Always run 'ls -la' first to check for symlink relationships before making any modifications.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _skills/FEEDBACK/SKILL.md
- Category: System Pattern

## Example entry
- Date/session: 15/03/2026 14:30:00
- Topic: Skill integration decision-making
- Lesson: When multiple skills are involved in a task, evaluate whether they complement each other or conflict. Consider the skill's primary purpose and how it interacts with others.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _skills/IMPROVE-SKILLS/SKILL-IMPROVE-SKILLS.md
- Category: Decision Pattern

## Example entry
- Date/session: 15/03/2026 15:15:00
- Topic: Workflow efficiency analysis
- Lesson: Identify bottlenecks in task execution by analyzing time spent on different activities. Look for patterns where the same tasks are performed repeatedly with similar inefficiencies.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _agent/HANDOFF.md
- Category: Process Pattern