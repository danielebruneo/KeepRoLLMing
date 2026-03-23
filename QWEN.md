# Qwen Bootstrap

This project uses **CATALYST**.

Before doing substantial work, read:
1. [AGENTS.md](AGENTS.md)
2. [_agent/knowledge/MAP.md](_agent/knowledge/MAP.md) - Agent navigation guide for CATALYST framework
3. [_agent/state/SCOPE.md](_agent/state/SCOPE.md)
4. [_agent/state/ACTIVE_TASK.md](_agent/state/ACTIVE_TASK.md)
5. [_agent/state/HANDOFF.md](_agent/state/HANDOFF.md)
6. [_agent/self/MEMORY.md](_agent/self/MEMORY.md)
7. [_project/KNOWLEDGE_BASE.md](_project/KNOWLEDGE_BASE.md) when relevant

Skills visible at runtime are projected into `.qwen/skills/`.
Those skills are markdown procedures, not Python executables.
Do **not** attempt to run `main.py` inside `.qwen/skills/`.

## Workflow Enforcement
- **[USE-CATALYST](.qwen/skills/USE-CATALYST/SKILL.md)** - Enforce CATALYST rules (auto-invoked when "catalyst" mentioned)
- **[WORKFLOW-CHECK](.qwen/skills/WORKFLOW-CHECK/SKILL.md)** - Validate task lifecycle compliance
- **[ENFORCE-SCOPE](.qwen/skills/ENFORCE-SCOPE/SKILL.md)** - Validate file changes against scope
- **[WORKFLOW-AUDIT](.qwen/skills/WORKFLOW-AUDIT/SKILL.md)** - Comprehensive state file review

## Pattern Learning
- **[CAPTURE-ENVIRONMENT-LESSON](.qwen/skills/CAPTURE-ENVIRONMENT-LESSON/SKILL.md)** - Save environmental/LLM patterns when workarounds found
- **[_agent/knowledge/ENVIRONMENT.md]**(_agent/knowledge/ENVIRONMENT.md) - Repository of environmental quirks and tool-calling patterns
- **Rule:** When first approach fails and you find a workaround, automatically save the pattern to ENVIRONMENT.md

## Qwen Added Memories
- When debugging logging issues: 1) Check actual log file contents first to see what's really being written, 2) Examine the logging function implementation to understand field filtering, 3) Compare what fields are passed vs what's included in output, 4) Restart server with fresh logs to verify fixes. A common pitfall is when log functions only include a hardcoded subset of fields (like req_id, model, endpoint), causing most events to appear "empty" in the file even though they're being written.
- CATALYST core skills were integrated on 2026-03-23: CONSOLIDATE-DOC, ENFORCE-CATALYST-WORKFLOW, ENFORCE-PROJECT-TODO, ENFORCE-PROJECT-WORKFLOW, IMPROVE-DOC, INDEX-SKILLS, LEARN-PROJECT, SAVE-MEMORY, plus SKILLS-INDEX.md. Knowledge base updated with cognitive workflow section (THINK→PLAN→WORK→FEEDBACK→LEARN→ADAPT→CLOSE-TASK sequence).
