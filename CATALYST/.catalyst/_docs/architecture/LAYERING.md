# Layering

CATALYST separates concerns into three main layers plus runtime projection:

## Core
`.catalyst/`
Reusable framework files, core skills, templates, and core docs.

## Agent instance
`_agent/`
- `state/` = runtime task continuity
- `self/` = local agent learning and self-brain
- `overlay/` = local overrides of core behavior
- `learning_reports/` = preserved learning session reports

## Project brain
`_project/`
Project-specific knowledge, maps, decisions, and project-only skills.

## Runtime projection
`.qwen/skills/`
Generated registry of visible runtime skills for Qwen.
