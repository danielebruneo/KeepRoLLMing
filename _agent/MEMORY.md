# Memory

Use this file for non-obvious lessons that are likely to matter again.

## Template Reference
This file follows the [MEMORY.template.md](../_templates/MEMORY.template.md) format for consistency.

## Topic: Template utilization in documentation
- Date/session: 15/03/2026 10:21:59
- Lesson: When creating new documentation elements, ensure they follow the established template format and cross-reference related components properly.
- Relevant files: _templates/ACTIVE_TASK.template.md, _docs/development/WORKFLOW.md

## Topic: Symlink handling in skill directories
- Date/session: 15/03/2026 12:47:00
- Lesson: When working with skills in CATALYST projects, be aware that some files like SKILL-FEEDBACK.md are symlinks to SKILL.md. Only edit the actual content file (SKILL.md) as both links point to the same content. Always run 'ls -la' first to check for symlink relationships before making any modifications.
- Relevant files: _skills/FEEDBACK/SKILL-FEEDBACK.md, _skills/FEEDBACK/SKILL.md